# zfs_luks
A convenience tool for managing ZFS inside LUKS containers. Given a declarative configuration file, it can create, open, or close
LUKS + ZFS device sets, and automate bind mounts once mounted. The base script is provided, as well as two example configs. 
Contributions welcome.

# Encrypted ZFS
When I initially wrote this tool, ZFS had no native support for encryption. While it's easy enough to setup ZFS across device mapper nodes,
remembering the details after infrequent uptime interruptions proved frustraiting. So, this script was born with (open, close) symmantics.
Recently (create) was added to allow for declarative configuration.

Configuration files specify one zpool with any number of devices and zvols. Topologically, the structure follows:
  - backing device(s) by UUID
  - LUKS encryption layer per device
  - zpool disk/mirror/raidz across all devices
  - zvols in the zpool
  - bind mount patterns
  
Behavior: Open/create construct the topology in the forward direction, close deconstructs the topology in the reverse direction.

# Configuration

Declaring mappings is as easy as:
  - Gather UUIDs of base devices.
  - Name the zpool and at least one zvol.
  - Add any bind mount patterns as desired.
  
Then run the tool. Look at example-*.cfg for inspiration.
