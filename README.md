# YAUCC - Yet Another Universal Config Collector 
This python 3.7 - 3.9 app be used for network survey, or any other operation or maintenance tasks. Tested with 3.9 version.  

It can:
- connect using SSHv2 to number of network devices
- execute series of show commands on network  devices 
- write output of executed commands into files in one folder 

## WARNING!!! YAUCC not tested for configuration commands, but there are no any special restrictions for them.

It created based on Scrapli python library and support it's core platforms:
- Cisco IOS-XE - cisco_iosxe
- Cisco NX-OS - cisco_nxos
- Cisco IOS-XR - cisco_iosxr
- Arista EOS - arista_eos
- Juniper JunOS - juniper_junos

** More platforms from Scraply-community will be added later.

It use only transport ssh2 -- scrapli wrapper around ssh2-python library. 
For using YACC you need:
1. Install python 3.7+ (latest - best choice) and reqiured libraries.
2. Rename '.env_init' file into '.env' file and change environment variables in it - see below (this file will remains locally).
3. Put actual devices information into 'devices.txt' file (see below).
4. Change file 'commands_<your_platform>.txt' file (if required).
5. Run script using:# <YAUCC_PATH>\python.exe -u -O <YAUCC_PATH>\main.py -d devices.txt
6. Look at console messages for errors and progress.
7. Get results in folder: <YAUCC_PATH>\output\cnf_<date>-<time>

##File 'devices.txt'
It is text file with records for each device:
```
device platform;device IP;device username;device password;device enable password;
```
Minimum form of a record:
```
;device IP;;;;
```
In this case device platform should be tried to be autodetected by app, login and password values (if absent) should be get from .env file.
Add line for each device you like to survey. File name, extension and location can be any - it passed as CLI value.
Example of devices file included into repo: 'devices_sample.txt'
File names, extension and location can be any - it is passed as CLI value.

Script provided with set of files with lists of 'show' commands for supported platforms. These commands should be executed on each device. 
These lists are excessive (not all commands are relevant for any device) and can produce errors in log. 
For unknown or not successfully autodetected platform, commands from 'default_commands.txt' file should be executed.