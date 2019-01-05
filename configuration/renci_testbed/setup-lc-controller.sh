#!/bin/bash

AW_REPO="https://github.com/RENCI-NRIG/atlanticwave-proto.git"
AW_BRANCH="master-rci"

while getopts "R:B:" opt; do
    case $opt in
        R)
            AW_REPO=${OPTARG}
            ;;
        B)
            AW_BRANCH=${OPTARG}
            ;;
    esac
done


# yum work
sudo yum -y update
sudo yum -y install git docker.io

#sudo groupadd docker #Already added
sudo usermod -aG docker $USER

# git work
echo "--- $0 : AW_REPO  : ${AW_REPO}"
echo "--- $0 : AW_BRANCH: ${AW_BRANCH}"
git clone ${AW_REPO}

# Docker work: build Local Controller containers
cd atlanticwave-proto
git checkout ${AW_BRANCH}
cp configuration/renci_testbed/renci_ben.manifest docker/lc_container/

#sudo systemctl restart docker
sudo service docker restart

cd docker/lc_container
sed -r -i "s/master/${AW_BRANCH}/g" Dockerfile
sudo docker build -t lc_container .
rm -f renci_ben.manifest 

# Copy over run scripts
cd ../../configuration/renci_testbed
cp start-lc-controller.sh ~
