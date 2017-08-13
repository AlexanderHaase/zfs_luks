#!/usr/bin/python

import os
import os.path
import subprocess
import re
import pwd
import logging
import argparse
import yaml
import itertools
import collections
import errno
from functools import partial

logger = logging.getLogger( __name__ )

class callmap( object ):
	'''like starmap, but handles dicts via kwargs'''
	def __init__( self, function, iteratable ):
		self.function = function
		self.iterator = iteratable.__iter__()

	def next( self ):
		val = self.iterator.next()
		if isinstance( val, dict ):
			ret = self.function( **val )
		elif hasattr( val, '__iter__' ):
			ret = self.function( *val )
		else:
			ret = self.function( val )

		return ret

	def __iter__( self ):
		return self


def consume(iterator, n = None ):
    "Advance the iterator n-steps ahead. If n is none, consume entirely."
    # Use functions that consume iterators at C speed.
    if n is None:
        # feed the entire iterator into a zero-length deque
        collections.deque(iterator, maxlen=0)
    else:
        # advance to the empty slice starting at position n
        next(itertools.islice(iterator, n, n), None)


class zol_crypt( object ):

	OPEN = 'open'
	CLOSE = 'close'
	CREATE = 'create'

	def __init__( self, defines = {} ):
		self.defines = defines
		self.mounts = {}
		self.devices = []
		self.simulate = False
		self.force = False

		# handle path formatting using fstype to split
		cmd = ( 'findmnt', '-t', 'zfs', '-o', 'SOURCE,FSTYPE,TARGET' )
		try:
			for line in subprocess.check_output( cmd ).splitlines()[ 1: ]:
				pair = line.split( 'zfs' )
				src = '/' + re.sub( "([\[\]])", "", pair[ 0 ].strip() )
				dst = pair[ 1 ].strip() 
				self.mounts[ dst ] = src

		except subprocess.CalledProcessError:
			logger.debug( "No mounts zfs found system-wide!" )

		logger.debug( "Existing mounts: {}".format( self.mounts ) )

		self.users = subprocess.check_output( ['ls','/home'] ).splitlines()
		logger.debug( "Users: {}".format( self.users ) )

	def runCommands( self, tag, action, commands ):
		if( commands is not None ):
			for command in commands:
				logger.info( "{} {}: {}".format( tag, action, command ) )
				if( not self.simulate ):
					subprocess.check_call( command )


	mapperDir = "/dev/mapper/"
	uuidDir = "/dev/disk/by-uuid/"
	cryptDir = "/dev/zol_crypt/"

	def configLUKS( self, action, uuid ):
		'''Entry point for the LUKS config directive'''
		devPath = self.uuidDir + uuid
		mapper = "crypt-" + uuid
		mapperPath = self.mapperDir + mapper
		cryptPath = self.cryptDir + mapper

		mapperExists = os.path.exists( mapperPath )
		

		if( action == self.OPEN and not mapperExists ):
			self.mkdirs( 'root', self.cryptDir )
			commands = [
				( 'cryptsetup', 'luksOpen', devPath, mapper ),
				( 'ln', '-s', mapperPath, cryptPath )
				]
		elif( action == self.CLOSE and mapperExists ):
			commands = [
				( 'cryptsetup', 'luksClose', mapper ),
				( 'rm', cryptPath )
				]
		elif( action == self.CREATE and ( not mapperExists or self.force ) ):
			commands = [
				( 'cryptsetup', 'luksFormat', devPath, '--uuid=' + uuid ),
				( 'cryptsetup', 'luksOpen', devPath, mapper ),
				( 'ln', '-s', mapperPath, cryptPath )
				]
		else:
			commands = []

		self.runCommands( 'LUKS', action, commands )
		self.devices.append( cryptPath )


	def configZFS( self, action, tag, volume ):
		'''Entry point for the ZFS config directive'''
		self.zpool( action, volume.split( '/' )[ 0 ] )

		volumeExists = os.path.isdir( '/' + volume )

		if( action == self.CREATE and not volumeExists ):
			self.runCommands( 'zfs', action, [ ( 'zfs', 'create', volume ) ] )

		logger.debug( "zfs: adding tag '{}' for '{}'".format( tag, volume ) )
		self.defines[ tag ] = '/' + volume


	zpoolModes = {
		1: tuple(),
		2: ('mirror',)
	}

	def zpool( self, action, pool ):
		'''Ensure zpool is in state for action'''
		poolPath = '/' + pool
		poolExists = self.mounts.get( poolPath, None ) == poolPath

		if( action == self.CREATE and (not poolExists or self.force) ):
			poolMode = self.zpoolModes.get( len( self.devices ), ('raidz',) )
			cmd = ( 'zpool', 'create', '-f', '-o', 'ashift=12', pool) +  poolMode + tuple(self.devices)
		elif( action == self.OPEN and not poolExists ):
			cmd = ( 'zpool', 'import', pool, '-d', self.cryptDir )
		elif( action == self.CLOSE and poolExists ):
			cmd = ( 'zpool', 'export', pool )
		else:
			cmd = None

		if cmd:
			self.runCommands( 'zpool', action, [ cmd ] )


	def configMount( self, action, src, dst, mkdirs ):
		'''Entry point for mounting--generate paths using defines'''

		if '{user}' in src or '{user}' in dst:
			users = self.users
		else:
			users = [ 'root' ]

		logger.debug( "mount {}: current defines: {}".format( action, self.defines ) )

		consume( itertools.imap( partial( self.mountUser, action, src, dst, mkdirs ), users ) )


	def mountUser( self, action, src, dst, mkdirs, user ):
		srcPath = src.format( user = user, **self.defines )
		dstPath = dst.format( user = user, **self.defines )

		if mkdirs:
			self.mkdirs( user, dstPath )

		self.mount( action, srcPath, dstPath )


	def mount( self, action, src, dst ):
		'''Handle action for mount src on dst as concrete paths'''
		mountExists = self.isMounted( src, dst )

		if( action == self.OPEN or action == self.CREATE ):
			cmd = ( 'mount', '-o', 'bind', src, dst ) if not mountExists else None 
		elif( action == self.CLOSE ):
			cmd = ( 'umount', dst ) if mountExists else None
		else:
			cmd = None

		if cmd:
			self.runCommands( 'mount', action, [ cmd ] )
			self.mounts[ dst ] = src


	def isMounted( self, src, dst ):
		'''Check if src is mounted on dst--cached lookup'''
		try:
			return self.mounts[ dst ] == src
		except KeyError:
			return False


	def mkdirs( self, user, path ):
		'''Ensure path exists and end node belongs to user'''
		try:
			os.makedirs( path )
		except OSError as error:
			if error.errno != errno.EEXIST:
				raise error

		userInfo = pwd.getpwnam( user )
		if( not self.simulate ):
			os.chown( path, userInfo[ 2 ], userInfo[ 3 ] )


	def getOrder( self, action ):
		order = [
			( 'LUKS', partial( self.configLUKS, action ) ),
			( 'ZFS', partial( self.configZFS, action ) ),
			( 'Mount', partial( self.configMount, action ) ),
			]

		if( action == self.OPEN or action == self.CREATE ):
			pass

		elif( action == self.CLOSE ):
			# parse zfs tags without action
			order.append( ( 'ZFS', partial( self.configZFS, None ) ) )
			order.reverse()

		else:
			order = None

		return order

	@classmethod
	def run( cls, action, path, **kwargs ):
		instance = cls()

		for key, value in kwargs.items():
			setattr( instance, key, value )

		with open( path, 'r' ) as handle:
			config = yaml.safe_load( handle )
			for key, func in instance.getOrder( action ):
				values = config.get( key )
				logger.debug( "{}: {}".format( key, values ) )
				if( values ):
					consume( callmap( func, values ) )

if __name__ == '__main__':
	logger = logging.getLogger()
	logger.setLevel(logging.DEBUG)
     
	# create console handler and set level to info
	handler = logging.StreamHandler()
	handler.setLevel( logging.DEBUG )
	handler.setFormatter( logging.Formatter( "%(asctime)s - %(levelname)s\t<%(name)s:%(lineno)d>: %(message)s" ) )
	logger.addHandler( handler )

	parser = argparse.ArgumentParser( description="Mount raid points" )
	parser.add_argument( '-v', '--verbose', default = "INFO" )
	parser.add_argument( '-f', '--force', action="store_true", default = False )
	parser.add_argument( '-s', '--simulate', action="store_true", default = False )
	parser.add_argument( 'action' )
	parser.add_argument( 'config' )

	args = parser.parse_args()
	logger.setLevel( getattr( logging, args.verbose ) )
	logger.debug( args )

	zol_crypt.run( args.action, args.config, force = args.force, simulate = args.simulate )

	
