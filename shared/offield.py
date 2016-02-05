# Copyright 2016 - Sean Donovan
# AtlanticWave/SDX Project

import sys

class FieldTypeError(TypeError):
    pass

class FieldValueError(ValueError):
    pass

class FieldPrereqError(ValueError):
    pass

class Field(object):
    ''' This is the parent class for different kinds of fields that are used in
        OpenFlowActions (defined below). It provides common structure and defines
        descriptors for each child class. '''
    
    def __init__(self, name, value=None, prereq=None, mask=False):
        ''' name is the name of the field, and is used for prerequisite
            checking.
            value is the value that this particular field is initialized with
            and can be changed by setting the value.
            prereq is an optional prerequisite. There are two uses, which are
            defined by which call one uses.
               - is_optional() - If the prereq condition is satisfied, than this
                 is a non-optional field. If None, this is a non-optional field. 
               - check_prerequisites() - If there is a prerequisite, and one of 
                 the prerequisites is not present in the allfields parameter, 
                 then this check fails. '''
        
        self._name = name
        self.value = value
        self.prereq = prereq

    #TODO - Can these be represented as a property/discriptor? I don't think so.
    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def __eq__(self, other):
        if type(self) != type(other):
            return False
        if ((self._name == other._name) and
            (self.value == other.value)):
            return True
        return False

    def check_validity(self):
        raise NotImplementedError("Subclasses must implement this.")

    def is_optional(self, allfields):
        ''' This checks the other fields in this particular action to see if 
            this is an optional field. If it is optional, returns True, if it is
            required, return False. '''
            
        if self.prereq == None:
            return False
        # Loop through all the fields
        for field in allfields:
            # If the field matches the prerequisites, then this is not an
            # optional field, return False.
            if self.prereq == field:
                return False
        # Seems it is optional.
        return True

    def check_prerequisites(self, allfields):
        ''' This checks to see if any of the prereqs exist in allfields that
            is passed in. If at least one of the prereqs are satisfied, the
            check passes. Otherwise, raises an error. '''
        if self.prereq == None:
            return
        for field in allfields:
            if field in self.prereq:
                return
        raise FieldPrereqError("Prerequisites are not met")
        
        

class number_field(Field):
    ''' Used for fields that need to be numbers. Has additional required init
        fields:
            min    - Minimum value that is allowed.
            max    - Maximum value that is allowed.
            others - Optional field that is a list of other values that are
                     valid.
    '''
    def __init__(self, name, min, max, value=None, prereq=None, others=[]):
        if value is not None:
            if type(value) is not int:
                raise FieldTypeError("value is not a number")
        
        super(number_field, self).__init__(name, value, prereq)

        self.min = min
        self.max = max
        self.others = others

    def check_validity(self):
        # Check if self.value is a number
        if not isinstance(self.value, int):
            raise FieldTypeError("self.value is not of type int")

        # Check if self.value is between self.min and self.max a
        if self.value < min or self.value > max:
             if len(self.others) == 0 :
                 raise FieldValueError(
                     "self.value is not between " + str(self.min) +
                     " and " + str(self.max))
             elif self.value not in self.others:
                 raise FieldValueError(
                     "self.value is not between " + str(self.min) +
                     " and " + str(self.max) + " and not in (" +
                     str(self.others) + ")")        


class bitmask_field(number_field):
    ''' Used for fields that need bitmasks. Same as a number_field right 
        now, but could change in the future. '''
    pass

class ipv4_field(Field):
    ''' Used for IPv4 addresses. '''
    
    def check_validity(self):
        # TODO: This needs to be written.
        pass

class ipv6_field(Field):
    ''' Used for IPv6 addresses. '''
    
    def check_validity(self):
        # TODO: This needs to be written.
        pass



