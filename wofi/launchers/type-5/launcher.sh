#!/usr/bin/env bash

## Author : Aditya Shakya (adi1090x)
## Github : @adi1090x
#
## Wofi (ported from Rofi): Launcher (Modi Drun, Run, File Browser, Window)
#
## Available Styles
#
## style-1     style-2     style-3     style-4     style-5

dir="$HOME/.config/wofi/launchers/type-5"
theme='style-1'

## Run
wofi \
    -show drun \
    -theme ${dir}/${theme}.rasi
