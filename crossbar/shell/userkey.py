###############################################################################
#
# Crossbar.io Shell
# Copyright (c) Crossbar.io Technologies GmbH. Licensed under EUPLv1.2.
#
###############################################################################

import re
import os
import binascii
import socket
from collections import OrderedDict

import getpass
import click

from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from eth_keys import KeyAPI
from eth_keys.backends import NativeECCBackend

import txaio

txaio.use_twisted()  # noqa

from autobahn.util import utcnow
from autobahn.wamp import cryptosign

from crossbar.shell.util import style_ok, style_error

if 'USER' in os.environ:
    _DEFAULT_EMAIL_ADDRESS = '{}@{}'.format(os.environ['USER'], socket.getfqdn())
else:
    _DEFAULT_EMAIL_ADDRESS = 'unknown'


class EmailAddress(click.ParamType):
    """
    Email address validator.
    """

    name = 'Email address'

    def __init__(self):
        click.ParamType.__init__(self)

    def convert(self, value, param, ctx):
        if re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", value):
            return value
        self.fail(style_error('invalid email address "{}"'.format(value)))


def _user_id(yes_to_all=False):
    if yes_to_all:
        return _DEFAULT_EMAIL_ADDRESS
    while True:
        value = click.prompt('Please enter your email address', type=EmailAddress(), default=_DEFAULT_EMAIL_ADDRESS)
        if click.confirm('We will send an activation code to {}, ok?'.format(style_ok(value)), default=True):
            break
    return value


def _creator(yes_to_all=False):
    """
    for informational purposes, try to identify the creator (user@hostname)
    """
    if yes_to_all:
        return _DEFAULT_EMAIL_ADDRESS
    else:
        try:
            user = getpass.getuser()
        except BaseException:
            user = 'unknown'

        try:
            hostname = socket.gethostname()
        except BaseException:
            hostname = 'unknown'

        return '{}@{}'.format(user, hostname)


def _write_user_key(filepath, tags, msg):
    """
    Internal helper, write the given tags to the given file-
    """
    with open(filepath, 'w') as f:
        f.write(msg)
        for (tag, value) in tags.items():
            if value:
                f.write('{}: {}\n'.format(tag, value))


def _parse_user_key_file(key_path: str, private: bool = True) -> OrderedDict:
    """
    Internal helper. This parses a node.pub or node.priv file and
    returns a dict mapping tags -> values.
    """
    if os.path.exists(key_path) and not os.path.isfile(key_path):
        raise Exception("Key file '{}' exists, but isn't a file".format(key_path))

    allowed_tags = ['public-key-ed25519', 'public-adr-eth', 'user-id', 'created-at', 'creator']
    if private:
        allowed_tags.extend(['private-key-ed25519', 'private-key-eth'])

    tags = OrderedDict()  # type: ignore
    with open(key_path, 'r') as key_file:
        got_blankline = False
        for line in key_file.readlines():
            if line.strip() == '':
                got_blankline = True
            elif got_blankline:
                tag, value = line.split(':', 1)
                tag = tag.strip().lower()
                value = value.strip()
                if tag not in allowed_tags:
                    raise Exception("Invalid tag '{}' in key file {}".format(tag, key_path))
                if tag in tags:
                    raise Exception("Duplicate tag '{}' in key file {}".format(tag, key_path))
                tags[tag] = value
    return tags


class UserKey(object):
    def __init__(self, privkey, pubkey, yes_to_all=True):

        self._privkey_path = privkey
        self._pubkey_path = pubkey

        self.key = None
        self._creator = None
        self._created_at = None
        self.user_id = None
        self._privkey = None
        self._privkey_hex = None
        self._pubkey = None
        self._pubkey_hex = None
        self._load_and_maybe_generate(self._privkey_path, self._pubkey_path, yes_to_all)

    def __str__(self):
        return 'UserKey(privkey="{}", pubkey="{}" [{}])'.format(self._privkey_path, self._pubkey_path,
                                                                self._pubkey_hex)

    def _load_and_maybe_generate(self, privkey_path, pubkey_path, yes_to_all=False):

        if os.path.exists(privkey_path):

            # node private key seems to exist already .. check!

            priv_tags = _parse_user_key_file(privkey_path, private=True)
            for tag in ['creator', 'created-at', 'user-id', 'public-key-ed25519', 'private-key-ed25519']:
                if tag not in priv_tags:
                    raise Exception("Corrupt user private key file {} - {} tag not found".format(privkey_path, tag))

            creator = priv_tags['creator']
            created_at = priv_tags['created-at']
            user_id = priv_tags['user-id']

            privkey_hex = priv_tags['private-key-ed25519']
            privkey = SigningKey(privkey_hex, encoder=HexEncoder)
            pubkey = privkey.verify_key
            pubkey_hex = pubkey.encode(encoder=HexEncoder).decode('ascii')

            if priv_tags['public-key-ed25519'] != pubkey_hex:
                raise Exception(("Inconsistent user private key file {} - public-key-ed25519 doesn't"
                                 " correspond to private-key-ed25519").format(pubkey_path))

            eth_pubadr = None
            eth_privkey = None
            eth_privkey_seed_hex = priv_tags.get('private-key-eth', None)
            if eth_privkey_seed_hex:
                eth_privkey_seed = binascii.a2b_hex(eth_privkey_seed_hex)
                eth_privkey = KeyAPI(NativeECCBackend).PrivateKey(eth_privkey_seed)
                eth_pubadr = eth_privkey.public_key.to_checksum_address()
                if 'public-adr-eth' in priv_tags:
                    if priv_tags['public-adr-eth'] != eth_pubadr:
                        raise Exception(("Inconsistent node private key file {} - public-adr-eth doesn't"
                                         " correspond to private-key-eth").format(privkey_path))

            if os.path.exists(pubkey_path):
                pub_tags = _parse_user_key_file(pubkey_path, private=False)
                for tag in ['creator', 'created-at', 'user-id', 'public-key-ed25519']:
                    if tag not in pub_tags:
                        raise Exception("Corrupt user public key file {} - {} tag not found".format(pubkey_path, tag))

                if pub_tags['public-key-ed25519'] != pubkey_hex:
                    raise Exception(("Inconsistent user public key file {} - public-key-ed25519 doesn't"
                                     " correspond to private-key-ed25519").format(pubkey_path))

                if pub_tags.get('public-adr-eth', None) != eth_pubadr:
                    raise Exception(
                        ("Inconsistent user public key file {} - public-adr-eth doesn't"
                         " correspond to private-key-eth in private key file {}").format(pubkey_path, privkey_path))

            else:
                # public key is missing! recreate it
                pub_tags = OrderedDict([
                    ('creator', priv_tags['creator']),
                    ('created-at', priv_tags['created-at']),
                    ('user-id', priv_tags['user-id']),
                    ('public-key-ed25519', pubkey_hex),
                    ('public-adr-eth', eth_pubadr),
                ])
                msg = 'Crossbar.io user public key\n\n'
                _write_user_key(pubkey_path, pub_tags, msg)

                click.echo('Re-created user public key from private key: {}'.format(style_ok(pubkey_path)))

            # click.echo('User public key loaded: {}'.format(style_ok(pubkey_path)))
            # click.echo('User private key loaded: {}'.format(style_ok(privkey_path)))

        else:
            # user private key does not yet exist: generate one
            creator = _creator(yes_to_all)
            created_at = utcnow()
            user_id = _user_id(yes_to_all)

            privkey = SigningKey.generate()
            privkey_hex = privkey.encode(encoder=HexEncoder).decode('ascii')
            pubkey = privkey.verify_key
            pubkey_hex = pubkey.encode(encoder=HexEncoder).decode('ascii')

            eth_privkey_seed = os.urandom(32)
            eth_privkey_seed_hex = binascii.b2a_hex(eth_privkey_seed).decode()
            eth_privkey = KeyAPI(NativeECCBackend).PrivateKey(eth_privkey_seed)
            eth_pubadr = eth_privkey.public_key.to_checksum_address()

            # first, write the public file
            tags = OrderedDict([
                ('creator', creator),
                ('created-at', created_at),
                ('user-id', user_id),
                ('public-key-ed25519', pubkey_hex),
                ('public-adr-eth', eth_pubadr),
            ])
            msg = 'Crossbar.io user public key\n\n'
            _write_user_key(pubkey_path, tags, msg)
            os.chmod(pubkey_path, 420)

            # now, add the private key and write the private file
            tags['private-key-ed25519'] = privkey_hex
            tags['private-key-eth'] = eth_privkey_seed_hex
            msg = 'Crossbar.io user private key - KEEP THIS SAFE!\n\n'
            _write_user_key(privkey_path, tags, msg)
            os.chmod(privkey_path, 384)

            click.echo('New user public key generated: {}'.format(style_ok(pubkey_path)))
            click.echo('New user private key generated ({}): {}'.format(style_error('keep this safe!'),
                                                                        style_ok(privkey_path)))

        # fix file permissions on node public/private key files
        # note: we use decimals instead of octals as octal literals have changed between Py2/3
        if os.stat(pubkey_path).st_mode & 511 != 420:  # 420 (decimal) == 0644 (octal)
            os.chmod(pubkey_path, 420)
            click.echo(style_error('File permissions on user public key fixed!'))

        if os.stat(privkey_path).st_mode & 511 != 384:  # 384 (decimal) == 0600 (octal)
            os.chmod(privkey_path, 384)
            click.echo(style_error('File permissions on user private key fixed!'))

        # load keys into object
        self._creator = creator
        self._created_at = created_at

        self._privkey = privkey
        self._privkey_hex = privkey_hex
        self._pubkey = pubkey
        self._pubkey_hex = pubkey_hex

        self._eth_pubadr = eth_pubadr
        self._eth_privkey_seed_hex = eth_privkey_seed_hex
        self._eth_privkey = eth_privkey

        self.user_id = user_id
        self.key = cryptosign.SigningKey(privkey)
