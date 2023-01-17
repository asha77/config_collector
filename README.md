# config_collector
network devices config collector



The available transport plugins are:
    system -- wrapper around OpenSSH/System available SSH binary
    telnet -- Python standard library telnetlib
    asynctelnet -- Python standard library asyncio stream
    asyncssh -- wrapper around asyncssh library
    ssh2 -- wrapper around ssh2-python library
    paramiko -- wrapper around paramiko library


Available drivers (from scrapi):
    Cisco IOS-XE 	IOSXEDriver 	AsyncIOSXEDriver 	cisco_iosxe
    Cisco NX-OS 	NXOSDriver 	AsyncNXOSDriver 	cisco_nxos
    Cisco IOS-XR 	IOSXRDriver 	AsyncIOSXRDriver 	cisco_iosxr
    Arista EOS 	EOSDriver 	AsyncEOSDriver 	arista_eos
    Juniper JunOS 	JunosDriver 	AsyncJunosDriver 	juniper_junos