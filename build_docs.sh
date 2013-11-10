#!/bin/bash

# Install pdoc with "pip install pdoc"

pdoc --overwrite --html process_isolation &&\
mv process_isolation.m.html doc/
