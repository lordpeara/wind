#!/usr/bin/env sh

cd $(dirname $0)

python -m unittest discover tests "*_test.py"
