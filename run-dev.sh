#!/bin/bash

podman build -t docuhide .
podman run --rm -it -v $PWD:$PWD -w $PWD --network=host --env-host --userns=keep-id localhost/docuhide:latest bash