#!/bin/bash

# Variables used for nearly everything
source variables.sh

# stop existing LC container
docker stop $LC_CONTAINTER_NAME

# Start new LC container
docker run -e MANIFEST=$MANIFEST_FILE -e SITE=$LC_SITE -e PYTHONPATH=$PYTHON_PATH -e SDXIP=$LC_SDXIP -e AWAVEDIR=$CONTAINER_AWAVE_DIRECTORY -p $LC_PORT:$LC_PORT -v $LC_VOLUMES -it --name=$LC_CONTAINER_NAME lc_container $LC_START_COMMAND



