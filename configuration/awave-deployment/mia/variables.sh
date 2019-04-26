#!/bin/bash

# The xxx_START_COMMAND variables can be empty.

LOCAL_AWAVE_DIRECTORY="~/atlanticwave-proto"
CONTAINER_AWAVE_DIRECTORY="/development"
MANIFEST_FILE="/development/configuration/awave-deployment/awave.manifest"
PYTHON_PATH=".:/development/"

LC_CONTAINER_NAME="mia_local_controller"
LC_VOLUMES="$LOCAL_AWAVE_DIRECTORY:$CONTAINER_AWAVE_DIRECTORY:rw"
LC_IP_ADDR="192.168.1.27"
LC_PORT="6653"
LC_SITE="mia"
LC_SDXIP="192.168.1.21"
LC_START_COMMAND=""


#SDX_CONTAINER_NAME="sox_sdx_controller"
#SDX_VOLUMES="$LOCAL_AWAVE_DIRECTORY:$CONTAINER_AWAVE_DIRECTORY:rw"
#SDX_IPADDR=""
#SDX_PORT=""
#SDX_LCPORT=""
#SDX_START_COMMAND=""

