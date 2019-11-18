# Copyright 2016 - Sean Donovan
# AtlanticWave/SDX Project


import cPickle as pickle

from threading import Timer, Lock, Thread
from datetime import datetime, timedelta

from lib.AtlanticWaveManager import AtlanticWaveManager
from AuthorizationInspector import AuthorizationInspector
from BreakdownEngine import BreakdownEngine
from ValidityInspector import ValidityInspector
from TopologyManager import TopologyManager

from shared.constants import *

# Define different states!
ACTIVE_RULE                 = 1
INACTIVE_RULE               = 2
EXPIRED_RULE                = 3
INSUFFICIENT_PRIVILEGES     = 4

def STATE_TO_STRING(state):
    if state == 1:
        return "ACTIVE RULE"
    elif state == 2:
        return "INACTIVE RULE"
    elif state == 3:
        return "EXPIRED RULE"
    elif state == 4:
        return "INSUFFICIENT PRIVILEGES"




class RuleManagerError(Exception):
    ''' Parent class, can be used as a catch-all for other errors '''
    pass

class RuleManagerTypeError(TypeError):
    ''' if there's a type error. '''
    pass

class RuleManagerValidationError(RuleManagerError):
    ''' When a validation fails, raise this. '''
    pass

class RuleManagerBreakdownError(RuleManagerError):
    ''' When a breakdown fails, raise this. '''
    pass

class RuleManagerAuthorizationError(RuleManagerError):
    ''' When a authorization fails, raise this. '''
    pass

def TESTING_CALL(param):
    ''' RuleManager requires two parameters for proper initialization. However
        we also want for the REST API to be able to get a copy of the RuleManger
        easily.'''
    raise RuleManagerError("RuleManager has not been properly initialized")

class RuleManager(AtlanticWaveManager):
    ''' The RuleManager keeps track of all rules that are installed (and their 
        metadata), the breakdowns of the abstract rule per local controller as 
        created by the BreakdownEngine, as well as orchestrating new rule 
        requests and removals.
        Workflow: The REST API will send new rules by participants who are 
        allowed to send rules (of any type), it will be sent to the 
        ValididyInspector to check if it is a valid rule, then sent to the 
        BreakdownEngine to break it into constituent parts, and finally the 
        RuleManager will check with the AuthorizationInspector to see if the 
        breakdowns are allowed to be installed by that particular user. 
        Singleton. ''' 
    
    
    def __init__(self, db_filename, loggeridprefix='sdxcontroller',
                 send_user_rule_breakdown_add=TESTING_CALL,
                 send_user_rule_breakdown_remove=TESTING_CALL):
        # The params are used in order to maintain import hierarchy.
        loggerid = loggeridprefix + ".rulemanager"
        super(RuleManager, self).__init__(loggerid)
        
        # Setup timers and their associated locks.
        # Timer code in part based on https://github.com/sdonovan1985/py-timer
        self.install_timer = None
        self.install_next_time = None
        self.install_lock = Lock()
        self.remove_timer = None
        self.remove_next_time = None
        self.remove_lock = Lock()

        # Start database
        db_tuples = [('rule_table','rules'), ('config_table', 'config')]
        self._initialize_db(db_filename, db_tuples)

        # Rule Table cleanup
        try:
            # Need to cleanup autogenerated rules. These cannot be used after
            # reboot, as they may be outdated: we cannot tell, so we must delete
            self.rule_table.delete(user=AUTOGENERATED_USERNAME)
        except:
            # No big deal, there may not have been any.
            pass

        print "Rules present at initialization:"
        for rule in self.rule_table:
            print "   - %s" % rule
        # Used for filtering of rule_table
        self._valid_table_columns = ['hash', 'ruletype', 'user',
                                     'state', 'starttime', 'stoptime']


        # Config table setup
        # Initialize rule counter. Used to track the rules as they are installed
        # It may be in the DB.
        rulenum = self.config_table.find_one(key='rule_number')
        if rulenum == None:
            self.rule_number = 1
            self.config_table.insert({'key':'rule_number',
                                      'value':self.rule_number})
        else:
            self.rule_number = rulenum['value']

        print "Rule number = %d" % self.rule_number

        last_modified = self.config_table.find_one(key='last_modified')
        if last_modified == None:
            now = datetime.now()
            last_modified = now.strftime(rfc3339format)
            self.config_table.insert({'key':'last_modified',
                                      'value':last_modified})

        # Use these to send the rule to the Local Controller
        self.set_send_add_rule(send_user_rule_breakdown_add)
        self.set_send_rm_rule(send_user_rule_breakdown_remove)

        # Setup callback lists:
        self.install_callbacks = []
        self.remove_callbacks = []

        self.logger.warning("%s initialized: %s" % (self.__class__.__name__,
                                                    hex(id(self))))
        
    def set_send_add_rule(self, fcn):
        self.send_user_add_rule = fcn

    def set_send_rm_rule(self, fcn):
        self.send_user_rm_rule = fcn

    def add_rule(self, rule):
        ''' Adds a rule for a particular user. Returns rule hash if successful, 
            failure message based on why the rule installation failed. Also 
            returns a reference to the rule (e.g., a tracking number) so that 
            more details can be retrieved in the future. '''

        self.logger.info("add_rule: Beging with rule: %s" % rule)
        self._update_last_modified_timestamp()
        try:
            breakdown = self._determine_breakdown(rule)
        except Exception: raise
        self.dlogger.info("add_rule: breakdowns %s" % breakdown)        

        # If everything passes, set the hash, cookie, and breakdown,
        # put into database and call install_callbacks
        rulehash = self._get_new_rule_number()
        rule.set_rule_hash(rulehash)
        for entry in breakdown:
            entry.set_cookie(rulehash)
        rule.set_breakdown(breakdown)
        self.dlogger.info("add_rule: hash and cookies set to %s" % rulehash)

        rule.pre_add_callback(TopologyManager(), AuthorizationInspector())
        self._add_rule_to_db(rule)

        self._call_install_callbacks(rule)
        self.dlogger.info("add_rule: Rule added to db: %s" % rule)

        return rulehash
        

    def test_add_rule(self, rule):
        ''' Similar to add rule, save for actually pushing the rule to the local
            controllers. Useful for testing out whether a rule will be added as 
            expected, or to preview what rules will be pushed to the local 
            controller(s). '''
        try:
            breakdown = self._determine_breakdown(rule)
        except Exception as e: raise

        return breakdown

    def remove_rule(self, rule_hash, user):
        ''' Removes the rule that corresponds to the rule_hash that wa returned 
            either from add_rule() or found with get_rules(). If user does not 
            have removal ability, returns an error. '''
        if self.rule_table.find_one(hash=rule_hash) == None:
            raise RuleManagerError("rule_hash doesn't exist: %s" % rule_hash)

        self._update_last_modified_timestamp()
        rule = pickle.loads(str(self.rule_table.find_one(hash=rule_hash)['rule']))
        authorized = None
        try:
            authorized = AuthorizationInspector().is_authorized(user, rule) #FIXME
        except Exception as e:
            raise RuleManagerAuthorizationError("User %s is not authorized to remove rule %s with exception %s" % (user, rule_hash, str(e)))
        if authorized != True:
            raise RuleManagerAuthorizationError("User %s is not authorized to remove rule %s" % (user, rule_hash))

        rule.pre_remove_callback(TopologyManager(),
                                 AuthorizationInspector())
        self._rm_rule_from_db(rule)
        self._call_remove_callbacks(rule)

    def remove_all_rules(self, user):
        ''' Removes all rules. Just an alias for repeatedly calling 
            remove_rule() without needing to know all the hashes. '''
        for rule in self.rule_table.find():
            parsed_rule = pickle.loads(str(rule['rule']))
            # Skip autogenerated rules
            if parsed_rule.get_user() == AUTOGENERATED_USERNAME:
                continue
            self.remove_rule(rule['hash'], user)

    def get_rules_search_fields(self):
        ''' This returns fields that can be used in get_rules()'s filter.
            Basically, this is any of the columns used in the 
            rule_table.insert() action. There is some variation (the rule is 
            pickled, so not useful), but it tracks most of the colunmns. '''

        #FIXME: make this more general, so that it can actually search for something like "starts between 8 and 10am". Further, should be able to tell users what values are valid for particular fields. the RuleRegistry should help out here.

        return self._valid_table_columns

    def get_rules(self, filter={}, ordering=None):
        ''' Used for searching for rules based on a filter. The filter could be 
            based on the rule type, the user that installed the rule, the local 
            controllers that have rules installed, or the hash_value of the 
            rule. This will be useful for both administrators and for 
            participants for debugging. This will return a list of tuples 
            (rule_hash, json version of rule, ruletype, user, state as string). 

            filter is a dictionary with any of the fields that belong to what is
            returned by get_rules_search_fields(). Optional.

            orderring is the field to order the results by. For reversing, 
            adding a - to the front is valid. E.g., "hash" will return in 
            ascending order, while "-hash" will return in decending. 
            Optional.'''
        #FIXME: make this more general, so that it can actually search for something like "starts between 8 and 10am".
        
        # Validate that the filter is valid.
        if filter != None:
            if type(filter) != dict:
                raise RuleManagerTypeError("filter is not a dictionary: %s" % 
                                           type(filter))
            for key in filter.keys():
                if key not in self._valid_table_columns:
                    raise RuleManagerValidationError("filter column '%s' is not a valid filtering field %s" % (key, self._valid_table_columns))

        # Handle ordering, if necessary.
        if ordering != None:
            filter['order_by'] = ordering
        
        # Do the search on the table
        results = self.rule_table.find(**filter)


        #FIXME: need to figure out what to send back to the caller of the rules. What does the rule look like? Should it be the JSON version? I think so.
        retval = [(x['hash'],
                   pickle.loads(str(x['rule'])).get_json_rule(),
                   x['ruletype'],
                   pickle.loads(str(x['rule'])).get_user(),
                   STATE_TO_STRING(str(x['state']))) for x in results]
        return retval

    def get_breakdown_rules_by_LC(self, lc):
        ''' This gets broken down rules for a particular LC. Used at connection 
            startup by the SDXController. 
            Returns a list of broken down rules. 
        '''
        bd_list = []
        # Get all rules
        all_rules = self.rule_table.find()
        # For each rule, look at each breakdown
        for table_entry in all_rules:
            rule = pickle.loads(str(table_entry['rule']))
            for bd in rule.get_breakdown():
                # If Breakdown is for this LC, add to bd_list
                rule_lc = bd.get_lc()
                if rule_lc == lc:
                    bd_list += bd.get_list_of_rules()
        return bd_list
    
    def get_rule_details(self, rule_hash):
        ''' This will return details of a rule, including the rule itself, the 
            local controller breakdowns, and the user who installed the rule. 
        
            Returns tuple (rule_hash, json version of rule, ruletype, state, 
              user, list of text versions of breakdowns)
        '''
        table_entry = self.rule_table.find_one(hash=rule_hash)
        if table_entry != None:
            rule = pickle.loads(str(table_entry['rule']))
            
            # get the pieces
            jsonrule = rule.get_json_rule()
            ruletype = rule.get_ruletype()
            state = STATE_TO_STRING(table_entry['state'])
            user = rule.get_user()

            breakdowns = []
            for bd in rule.get_breakdown():
                lc = bd.get_lc()
                breakdowns += ["%s:%s" % (lc, str(x)) for x in 
                               bd.get_list_of_rules()]
            
            # Return as a tuple
            return (rule_hash, jsonrule, ruletype, state, user, breakdowns)

        return None

    def get_raw_rule(self, rule_hash):
        ''' This will return the actual rule, for advanced manipulation. '''
        table_entry = self.rule_table.find_one(hash=rule_hash)
        if table_entry != None:
            rule = pickle.loads(str(table_entry['rule']))
            return rule
        return None

    def register_for_rule_updates(self, install_callback, remove_callback):
        ''' Callback will be called when there is a rule update (install or 
            delete. 
            install_callback will be called when there's a new rule installed.
            remove_callback will be called when a rule is removed.
            Both callbacks can be the same function.
        '''
        if install_callback != None:
            self.install_callbacks.append(install_callback)
        if remove_callback != None:
            self.remove_callbacks.append(remove_callback)

    def unregister_for_topology_updates(self, install_callback=None,
                                        remove_callback=None):
        ''' Remove callback from list of callbacks to be called. '''
        if install_callback != None:
            try:
                self.install_callbacks.remove(install_callback)
            except:
                raise RuleManagerError("Trying to remove %s, not in install_callbacks: %s" % (install_callback, self.install_callbacks))
        if remove_callback != None:
            try:
                self.remove_callbacks.remove(remove_callback)
            except:
                raise RuleManagerError("Trying to remove %s, not in remove_callbacks: %s" % (remove_callback, self.remove_callbacks))

    def _call_install_callbacks(self, rule):
        ''' Call all install_callbacks. '''
        for cb in self.install_callbacks:
            cb(rule)

    def _call_remove_callbacks(self, rule):
        ''' Call all remove_callbacks. '''
        for cb in self.remove_callbacks:
            cb(rule)

    def _get_new_rule_number(self):
        ''' Returns a new rule number for use. For now, it's incrementing by 
            one, but this can be a security risk, so should be a random 
            number/hash.
            Good for 4B (or more!) rules!
        '''
        self.rule_number += 1
        self.config_table.update({'key':'rule_number', 
                                  'value':self.rule_number},
                                 ['key'])
        return self.rule_number

    def _determine_breakdown(self, rule):
        ''' This performs the bulk of the add_rule() and test_add_rule() 
            processing, including all the authorization checking. 
            Raises error if there are any problems.
            Returns breakdown of the rule if successful. '''

        valid = None
        breakdown = None
        authorized = None
        # Check if valid rule
        try:
            valid = ValidityInspector().is_valid_rule(rule)
        except Exception as e:
            self.dlogger.error("Caught Error for rule %s" % rule)
            self.exception_tb(e)
            raise
        
        if valid != True:
            raise RuleManagerValidationError(
                "Rule cannot be validated: %s" % rule)
        
        # Get the breakdown of the rule
        breakdown = BreakdownEngine().get_breakdown(rule)
        try:
            breakdown = BreakdownEngine().get_breakdown(rule)
        except Exception as e:
            self.dlogger.error("Caught Error for rule %s" % rule)
            self.exception_tb(e)
            raise

        if breakdown == None:
            raise RuleManagerBreakdownError(
                "Rule was not broken down: %s" % rule)

        # Check if the user is authorized to perform those actions.
        try:
            authorized = AuthorizationInspector().is_authorized(rule.username, rule)
        except Exception as e:
            self.dlogger.error("Caught Error for rule %s" % rule)
            self.exception_tb(e)
            raise
            
        if authorized != True:
            raise RuleManagerAuthorizationError(
                "Rule is not authorized: %s" % rule)

        return breakdown

    def _update_last_modified_timestamp(self):
        ''' Used for setting the last_modified timestamp. '''
        now = datetime.now()
        last_modified = now.strftime(rfc3339format)
        self.config_table.update({'key':'last_modified',
                                  'value':last_modified},
                                 ['key'])

    def get_last_modified_timestamp(self):
        ''' Get the last_modified timestamp. '''
        last_modified_dict = self.config_table.find_one(key='last_modified')
        last_modified = last_modified_dict['value']
        return last_modified

    def _add_rule_to_db(self, rule):
        ''' Adds rule to the database, which also include handling timed 
            insertion of rules. '''
        self.dlogger.info("_add_rule_to_db: %s:%s" % (rule,
                                                      rule.get_rule_hash()))
        state = INACTIVE_RULE

        # Should this be installed now? e.g., Is the begin time before *now*?
        # Set state based on this question.
        # Many rules do *not* have timers associated. If so, set the 
        # install_time to now and remove_time to None. Need to handle these
        # cases below.
        now = datetime.now()
        install_time = now
        remove_time = None
        if rule.get_start_time() != None:
            install_time = datetime.strptime(rule.get_start_time(), 
                                             rfc3339format)
        if rule.get_stop_time() != None:
            remove_time  = datetime.strptime(rule.get_stop_time(), 
                                             rfc3339format)

#        print "Now     : %s" % now
#        print "Install : %s" % install_time
#        print "Remove  : %s" % remove_time

        if remove_time != None and now >= remove_time:
            self.dlogger.info("  EXPIRED_RULE")
            state = EXPIRED_RULE
        elif now >= install_time: # implicitly, before remove_time
            state = ACTIVE_RULE
            self.dlogger.info("  ACTIVE_RULE")
            self._install_rule(rule)
        else:
            self.dlogger.info("  FUTURE RULE, still INACTIVE")

        # Push into DB.
        # If there are any changes here, update self._valid_table_columns.
        self.rule_table.insert({'hash':rule.get_rule_hash(), 
                                'rule':pickle.dumps(rule),
                                'ruletype':rule.get_ruletype(),
                                'user':rule.get_user(),
                                'state':state,
                                'starttime':rule.get_start_time(),
                                'stoptime':rule.get_stop_time(),
                                'extendedbd':pickle.dumps(None)})

        # Restart install timer if it's a rule starting the future
        if state == INACTIVE_RULE:
            self.dlogger.info("  INACTIVE_RULE")
            self._restart_install_timer()


    def _rm_rule_from_db(self, rule):
        ''' Removes rule from the database, which also includes cancelling any
            outstanding timed installations of the rule. '''

        # Find rule in DB, get important information: state, start/stop time
        record = self.rule_table.find_one(hash=rule.get_rule_hash())
        state = record['state']
        starttime = record['starttime']
        stoptime = record['stoptime']

        if state == ACTIVE_RULE:
            self._remove_rule(rule)
            self.rule_table.delete(hash=rule.get_rule_hash())

            if stoptime == self.remove_next_time:
                self._restart_remove_timer()
                
        # If inactive, 
        # Was it the next install timer to pop? If so, update timer.
        elif state == INACTIVE_RULE:
            self.rule_table.delete(hash=rule.get_rule_hash())
            self._restart_install_timer()

        # If Expired:
        # Nothing specific to do right now
        elif state == EXPIRED_RULE:
            self.rule_table.delete(hash=rule.get_rule_hash())
            pass
            #FIXME: Recurrent rules are weird. 

        # If Insufficient Privileges:
        # Nothing specific to do right now
        elif state == INSUFFICIENT_PRIVILEGES:
            pass


    def _install_rule(self, rule):
        ''' Helper function that installs a rule into the switch. '''
        try:
            self.dlogger.debug("_install_rule: %s:%d" % (rule,
                                                         rule.get_rule_hash()))
            self._reserve_resources(rule.get_resources())
            self._install_breakdown(rule.get_breakdown())
        except Exception as e: raise
        self._restart_remove_timer()

    def _reserve_resources(self, resource_list):
        ''' Helper function that reserves resources that a rule needs. '''
        for resource in resource_list:
            self.logger.debug("_reserve_resources: %s" % resource)
            TopologyManager().reserve_resource(resource)
        
    def _unreserve_resources(self, resource_list):
        ''' Helper function that unreserves resources that a rule needs. '''
        for resource in resource_list:
            self.logger.debug("_unreserve_resources: %s" % resource)
            TopologyManager().unreserve_resource(resource)

    def _install_breakdown(self, breakdown):
        try:
            for bd in breakdown:
                self.logger.debug("Sending install breakdown: %s" % bd)
                for rule in bd.get_list_of_rules():
                    self.logger.debug("    %s" % str(rule))
                self.send_user_add_rule(bd)
        except Exception as e: raise

    def _remove_rule(self, rule):
        ''' Helper function that remove a rule from the switch. '''
        try:
            table_entry = self.rule_table.find_one(hash=rule.get_rule_hash())
            extendedbd = pickle.loads(str(table_entry['extendedbd']))
            for bd in rule.get_breakdown():
                self.logger.debug("Sending remove breakdown: %s" % bd)
                self.send_user_rm_rule(bd)
            if extendedbd != None:
                for bd in extendedbd:
                    self.logger.debug("Sending remove extended breakdown: %s" % bd)
                    self.send_user_rm_rule(bd)
            self._unreserve_resources(rule.get_resources())
        except Exception as e: raise
        
    def _rule_install_timer_cb(self):
        ''' This is the timer callback for rule installation. Called when the 
            rule install timer pops. '''

        # Get list of future rules ordered by install time
        rules = self.rule_table.find(state = INACTIVE_RULE,
                                     order_by = "starttime")

        now = datetime.now()
        next_install_time = None

        # Does first rule in the list need installing? At least one should.
        for rule in rules:
            install_time = datetime.strptime(rule['starttime'],
                                             rfc3339format)
            # If this rule should be installed later, we're done with the loop
            # thanks to rules being in order by start time. First, save off time
            # for the next rule to be installed.
            if now < install_time:
                next_install_time = rule['starttime']
                break

            # Install rule and update state.
            self.rule_table.update({'hash':rule['hash'],
                                    'state':ACTIVE_RULE}, 
                                   ['hash'])
            
            self._install_rule(pickle.loads(str(rule['rule'])))
            
        
        # Set timer for next rule install, if necessary.
        self._restart_install_timer()            

    def _rule_remove_timer_cb(self):
        ''' This is the timer callback for rule removal (expiring rules). Called
            when the rule removal timer pops. '''

        # Get the list of existing rules, ordered by expiry time.
        rules = self.rule_table.find(state = ACTIVE_RULE,
                                     order_by = "stoptime")

        now = datetime.now()
        next_remove_time = None

        # Is the first rule in the list also expired? At least one should be.
        for rule in rules:
            remove_time = datetime.strptime(rule['stoptime'],
                                            rfc3339format)
            
            # If this rule should be removed later, we're done with the loop
            # thanks to rules being in order by stop time. First, save off time
            # for the next rule to be installed.
            if now < remove_time:
                next_remove_time = rule['stoptime']
                break

            # Remove rule and update state.
            self.rule_table.update({'hash':rule['hash'],
                                    'state':EXPIRED_RULE}, 
                                   ['hash'])
            self._remove_rule(rule)
            # FIXME: Recurrant rules will need to be updated on the install list potentially.

        # Set timer for next rule removal, if necessary
        self._restart_remove_timer()

    def _restart_remove_timer(self):
        ''' Internal function for starting the remove timer. ''' 
        # Is the rule active? If so, remove it from the LCs.
        # Was it the next remove timer to pop? If so, need to update timer.
        with self.remove_lock:
            # Clean up existing remove timer
            if self.remove_timer != None:
                self.remove_timer.cancel()
            self.remove_timer = None
            self.remove_next_time = None

            # Pull next active rules, and reset the timer.
            active_rules = self.rule_table.find(state=ACTIVE_RULE,
                                                order_by="stoptime")
            #, _limit=1)

            # If there are no new active rules, this was just be skipped
            for r in active_rules:
                # If it's a rule that lasts forever, skip it
                if r['stoptime'] == None:
                    continue
                # We only want the first one (the soonest to stop), so
                # there's a break at the end of this loop.
                now = datetime.now()
                self.remove_next_time = r['stoptime']
                delta = (datetime.strptime(self.remove_next_time,
                                           rfc3339format) - now)
                self.remove_timer = Timer(delta.total_seconds(),
                                          self._rule_remove_timer_cb)
                self.remove_timer.daemon = True
                self.remove_timer.start()
                break

    def _restart_install_timer(self):
        with self.install_lock:
            # Clean up existing install timer
            if self.install_timer != None:
                self.install_timer.cancel()
            self.install_timer = None
            self.install_next_time = None

            # Pull next inactive rule, and reset the timer.
            inactive_rules = self.rule_table.find(state=INACTIVE_RULE,
                                                  order_by="starttime", 
                                                  _limit=1)

            # If there are no new active rules, this was just be skipped
            for r in inactive_rules:
                now = datetime.now()
                self.install_next_time = r['starttime']
                delta = (datetime.strptime(self.install_next_time,
                                           rfc3339format) - now)
                self.install_timer = Timer(delta.total_seconds(),
                                           self._rule_install_timer_cb)
                self.install_timer.daemon = True
                self.install_timer.start()
                
    def change_callback_dispatch(self, cookie, data):
        ''' This is used to handle changes callbacks. It performs four main 
            functions:
              - Find the policy that this change callback belongs to
              - Call the change callback with data, possibly receive breakdown
                to install.
              - if received breakdown, install it
              - if received breakdown, update database of installed additional 
                breakdown.
        '''
        table_entry = self.rule_table.find_one(hash=cookie)
        if table_entry == None:
            raise RuleManagerError("rule_hash doesn't exist: %s" % cookie)

        policy = pickle.loads(str(table_entry['rule']))

        breakdown = policy.switch_change_callback(TopologyManager(),
                                                  AuthorizationInspector(),
                                                  data)
        if breakdown == None:
            return

        self.logger.debug("_change_callback_dispatch %s"% cookie)
        self._install_breakdown(breakdown)

        extendedbd = pickle.loads(str(table_entry['extendedbd']))
        if extendedbd == None:
            extendedbd = breakdown
        else:
            for entry in breakdown:
                extendedbd.append(entry)

        self.rule_table.update({'hash':table_entry['hash'],
                                'extendedbd':pickle.dumps(extendedbd)},
                               ['hash'])

