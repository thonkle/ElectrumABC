# -*- coding: utf-8 -*-
# -*- mode: python3 -*-
#
# Electrum ABC - lightweight eCash client
# Copyright (C) 2020 The Electrum ABC developers
# Copyright (C) 2016 Thomas Voegtlin
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import os
import sys
import traceback
from typing import Callable

from . import bitcoin
from . import keystore
from . import mnemo
from . import util
from .storage import (
    STO_EV_USER_PW,
    STO_EV_XPUB_PW,
    get_derivation_used_for_hw_device_encryption
)
from .wallet import (ImportedAddressWallet, ImportedPrivkeyWallet,
                     Standard_Wallet, Multisig_Wallet, wallet_types)
from .i18n import _
from .constants import PROJECT_NAME, REPOSITORY_URL, CURRENCY

# hardware device setup purpose
HWD_SETUP_NEW_WALLET, HWD_SETUP_DECRYPT_WALLET = range(0, 2)

class BaseWizard(util.PrintError):

    def __init__(self, config, storage):
        super(BaseWizard, self).__init__()
        self.config = config
        self.storage = storage
        self.wallet = None
        self.stack = []
        self.plugin = None
        self.keystores = []
        self.is_kivy = config.get('gui') == 'kivy'
        self.seed_type = None

    def run(self, *args):
        action = args[0]
        args = args[1:]
        self.stack.append((action, args))
        if not action:
            return
        if type(action) is tuple:
            self.plugin, action = action
        if self.plugin and hasattr(self.plugin, action):
            f = getattr(self.plugin, action)
            f(self, *args)
        elif hasattr(self, action):
            f = getattr(self, action)
            f(*args)
        else:
            raise BaseException("unknown action", action)

    def can_go_back(self):
        return len(self.stack)>1

    def go_back(self):
        if not self.can_go_back():
            return
        self.stack.pop()
        action, args = self.stack.pop()
        self.run(action, *args)

    def new(self):
        name = os.path.basename(self.storage.path)
        title = _("Create") + ' ' + name
        message = '\n'.join([
            _("What kind of wallet do you want to create?")
        ])
        wallet_kinds = [
            ('standard',  _("Standard wallet")),
            ('multisig',  _("Multi-signature wallet")),
            ('imported',  _(f"Import {CURRENCY} addresses or private keys")),
        ]
        choices = [pair for pair in wallet_kinds if pair[0] in wallet_types]
        self.choice_dialog(title=title, message=message, choices=choices, run_next=self.on_wallet_type)

    def on_wallet_type(self, choice):
        self.wallet_type = choice
        if choice == 'standard':
            action = 'choose_keystore'
        elif choice == 'multisig':
            action = 'choose_multisig'
        elif choice == 'imported':
            action = 'import_addresses_or_keys'
        self.run(action)

    def choose_multisig(self):
        def on_multisig(m, n):
            self.multisig_type = "%dof%d"%(m, n)
            self.storage.put('wallet_type', self.multisig_type)
            self.n = n
            self.run('choose_keystore')
        self.multisig_dialog(run_next=on_multisig)

    def choose_keystore(self):
        assert self.wallet_type in ['standard', 'multisig']
        i = len(self.keystores)
        title = _('Add cosigner') + ' (%d of %d)'%(i+1, self.n) if self.wallet_type=='multisig' else _('Keystore')
        if self.wallet_type =='standard' or i==0:
            message = _('Do you want to create a new seed, or to restore a wallet using an existing seed?')
            choices = [
                ('create_standard_seed', _('Create a new seed')),
                ('restore_from_seed', _('I already have a seed')),
                ('restore_from_key', _('Use public or private keys')),
            ]
            if not self.is_kivy:
                choices.append(('choose_hw_device',  _('Use a hardware device')))
        else:
            message = _('Add a cosigner to your multi-sig wallet')
            choices = [
                ('restore_from_key', _('Enter cosigner key')),
                ('restore_from_seed', _('Enter cosigner seed')),
            ]
            if not self.is_kivy:
                choices.append(('choose_hw_device',  _('Cosign with hardware device')))

        self.choice_dialog(title=title, message=message, choices=choices, run_next=self.run)

    def import_addresses_or_keys(self):
        v = lambda x: keystore.is_address_list(x) or keystore.is_private_key_list(x, allow_bip38=True)
        title = _(f"Import {CURRENCY} Addresses")
        message = _(f"Enter a list of {CURRENCY} addresses (this will create a"
                    f" watching-only wallet), or a list of private keys.")
        if bitcoin.is_bip38_available():
            message += " " + _("BIP38 encrypted keys are supported.")
        self.add_xpub_dialog(title=title, message=message, run_next=self.on_import,
                             is_valid=v, allow_multi=True)

    def bip38_prompt_for_pw(self, bip38_keys):
        ''' Implemented in Qt InstallWizard subclass '''
        raise NotImplementedError('bip38_prompt_for_pw not implemented')

    def on_import(self, text):
        # create a temporary wallet and exploit that modifications
        # will be reflected on self.storage
        if keystore.is_address_list(text):
            w = ImportedAddressWallet.from_text(self.storage, text)
            # Until this point in the wallet creation process, the flow was common
            # for imported_privkey and imported_address. We now need it to be more
            # precise for the sake of the next step (create_wallet)
            self.wallet_type = "imported_addr"
        elif keystore.is_private_key_list(text, allow_bip38=True):

            bip38_keys = [k for k in text.split() if k and bitcoin.is_bip38_key(k)]
            if bip38_keys:
                decrypted = self.bip38_prompt_for_pw(bip38_keys)
                if not decrypted:
                    self.go_back()
                    return
                for b38, tup in decrypted.items():
                    wif, adr = tup
                    text = text.replace(b38, wif)  # kind of a hack.. but works. replace the bip38 key with the wif key in the text.

            w = ImportedPrivkeyWallet.from_text(self.storage, text,
                                                None)
            self.keystores = w.get_keystores()
            self.wallet_type = "imported_privkey"
            self.stack = []  # 'Back' button wasn't working anyway at this point, so we just force it to read 'Cancel' and this proceeds with no password set.
        else:
            return self.terminate()
        return self.run('create_wallet')

    def restore_from_key(self):
        if self.wallet_type == 'standard':
            v = keystore.is_master_key
            title = _("Create keystore from a master key")
            message = ' '.join([
                _("To create a watching-only wallet, please enter your master public key (xpub/ypub/zpub)."),
                _("To create a spending wallet, please enter a master private key (xprv/yprv/zprv).")
            ])
            self.add_xpub_dialog(title=title, message=message, run_next=self.on_restore_from_key, is_valid=v)
        else:
            i = len(self.keystores) + 1
            self.add_cosigner_dialog(index=i, run_next=self.on_restore_from_key, is_valid=keystore.is_bip32_key)

    def on_restore_from_key(self, text):
        k = keystore.from_master_key(text)
        self.on_keystore(k)

    def on_hw_wallet_support(self):
        ''' Derived class InstallWizard for Qt implements this '''

    def choose_hw_device(self, purpose=HWD_SETUP_NEW_WALLET):
        title = _('Hardware Keystore')
        # check available plugins
        support = self.plugins.get_hardware_support()
        if not support:
            msg = '\n'.join([
                _('No hardware wallet support found on your system.'),
                _('Please install the relevant libraries (eg python-trezor for Trezor).'),
            ])
            self.confirm_dialog(title=title, message=msg, run_next= lambda x: self.choose_hw_device(purpose))
            return
        # scan devices
        devices = []
        devmgr = self.plugins.device_manager
        for name, description, plugin in support:
            try:
                # FIXME: side-effect: unpaired_device_info sets client.handler
                u = devmgr.unpaired_device_infos(None, plugin)
            except:
                devmgr.print_error("error", name)
                continue
            devices += list(map(lambda x: (name, x), u))
        extra_button = None
        if sys.platform in ("linux", "linux2", "linux3"):
            extra_button = (_("Hardware Wallet Support..."), self.on_hw_wallet_support)
        if not devices:
            msgs = [
                _('No hardware device detected.') + '\n\n',
                _('To trigger a rescan, press \'Next\'.') + '\n\n'
            ]

            if sys.platform in ('win32', 'win64', 'windows'):
                msgs.append(_('Go to "Settings", "Devices", "Connected devices", and do "Remove device". Then, plug your device again.') + '\n')

            if sys.platform in ('linux', 'linux2', 'linux3'):
                msgs.append(_('You may try the "Hardware Wallet Support" tool (below).') + '\n')

            support_no_libs = [s for s in support if not s[2].libraries_available]
            if len(support_no_libs) > 0:
                msgs.append('\n' + _('Please install the relevant libraries for these plugins:') + ' ')
                msgs.append(', '.join(s[2].name for s in support_no_libs) + '\n')
                msgs.append(_('On most systems you can do so with this command:') + '\n')
                msgs.append('pip3 install -r contrib/requirements/requirements-hw.txt\n')

            msgs.append('\n' + _("If this problem persists, please visit:")
                        + f"\n\n     {REPOSITORY_URL}/issues")

            msg = ''.join(msgs)
            self.confirm_dialog(title=title, message=msg, run_next= lambda x: self.choose_hw_device(purpose), extra_button=extra_button)
            return
        # select device
        self.devices = devices
        choices = []
        for name, info in devices:
            state = _("initialized") if info.initialized else _("wiped")
            label = info.label or _("An unnamed {}").format(name)
            try: transport_str = str(info.device.path)[:20]
            except: transport_str = 'unknown transport'
            descr = "%s [%s, %s, %s]" % (label, name, state, transport_str)
            choices.append(((name, info), descr))
        msg = _('Select a device') + ':'
        self.choice_dialog(
            title=title,
            message=msg,
            choices=choices,
            run_next=lambda *args: self.on_device(*args, purpose=purpose),
            extra_button=extra_button
        )

    def on_device(self, name, device_info, *, purpose):
        self.plugin = self.plugins.find_plugin(name, force_load=True)
        try:
            self.plugin.setup_device(device_info, self, purpose)
        except OSError as e:
            self.show_error(_('We encountered an error while connecting to your device:')
                            + '\n\n"' + str(e) + '"\n\n'
                            + _('To try to fix this, we will now re-pair with your device.') + ' '
                            + _('Please try again.'))
            devmgr = self.plugins.device_manager
            devmgr.unpair_id(device_info.device.id_)
            self.choose_hw_device(purpose)
            return
        except BaseException as e:
            if str(e).strip():
                # This prevents showing an empty "UserCancelled" message
                self.print_error(traceback.format_exc())
                self.show_error(str(e))
            self.choose_hw_device()
            return
        if purpose == HWD_SETUP_NEW_WALLET:
            if self.wallet_type == 'multisig':
                # There is no general standard for HD multisig.
                # This is partially compatible with BIP45; assumes index=0
                default_derivation = "m/45'/0"
            else:
                # Use the BCH derivation path for now, until hardware wallets
                # start supporting BCHA
                default_derivation = keystore.bip44_derivation_bch(0)
            self.derivation_dialog(
                lambda x: self.run('on_hw_derivation', name, device_info, str(x)),
                default_derivation,
                is_hw_wallet=True)
        elif purpose == HWD_SETUP_DECRYPT_WALLET:
            derivation = get_derivation_used_for_hw_device_encryption()
            xpub = self.plugin.get_xpub(device_info.device.id_, derivation, 'standard', self)
            password = keystore.Xpub.get_pubkey_from_xpub(xpub, ())
            self.storage.decrypt(password)
        else:
            raise Exception('unknown purpose: %s' % purpose)

    def derivation_dialog(self,
                          run_next: Callable,
                          default_derivation: str,
                          is_hw_wallet: bool = False,
                          seed: str = ''):
        bip44_btc = keystore.bip44_derivation_btc(0)
        bip44_bch = keystore.bip44_derivation_bch(0)
        bip44_xec = keystore.bip44_derivation_xec(0)
        lines = [
            _('Enter your wallet derivation here.'),
            _('If you are not sure what this is, leave this field unchanged.'),
            _(f"If you want the wallet to use legacy Bitcoin addresses use "
              f"{bip44_btc}"),
            _(f"If you want the wallet to use Bitcoin Cash addresses use "
              f"{bip44_bch}"),
            _(f"If you want the wallet to use {CURRENCY} addresses use "
              f"{bip44_xec}")]
        if is_hw_wallet:
            lines.append("\nAt this time, it is recommended to use the "
                         "Bitcoin Cash derivation path for hardware wallets,"
                         " unless you know that your device already supports"
                         " the new eCash derivation path.")
        message = '\n'.join(lines)
        scannable = self.wallet_type == "standard" and bool(seed)
        self.derivation_path_dialog(run_next=run_next, title=_(f'Derivation for {self.wallet_type} wallet'),
                                    message=message, default=default_derivation, test=bitcoin.is_bip32_derivation,
                                    seed=seed, scannable=scannable)

    def on_hw_derivation(self, name, device_info, derivation):
        from .keystore import hardware_keystore
        xtype = 'standard'
        try:
            xpub = self.plugin.get_xpub(device_info.device.id_, derivation, xtype, self)
        except BaseException as e:
            self.print_error(traceback.format_exc())
            self.show_error(str(e))
            return
        d = {
            'type': 'hardware',
            'hw_type': name,
            'derivation': derivation,
            'xpub': xpub,
            'label': device_info.label,
        }
        k = hardware_keystore(d)
        self.on_keystore(k)

    def passphrase_dialog(self, run_next):
        title = _('Seed extension')
        message = '\n'.join([
            _('You may extend your seed with custom words.') + " " + _("(aka 'passphrase')"),
            _('Your seed extension must be saved together with your seed.'),
        ])
        warning = '\n'.join([
            _('Note that this is NOT your encryption password.'),
            _('If you do not know what this is, leave this field empty.'),
        ])
        self.line_dialog(title=title, message=message, warning=warning, default='', test=lambda x:True, run_next=run_next)

    def restore_from_seed(self):
        self.opt_bip39 = True
        self.opt_ext = True
        test = mnemo.is_seed # TODO FIX #bitcoin.is_seed if self.wallet_type == 'standard' else bitcoin.is_new_seed
        self.restore_seed_dialog(run_next=self.on_restore_seed, test=test)

    def on_restore_seed(self, seed, is_bip39, is_ext):
        self.seed_type = 'bip39' if is_bip39 else mnemo.seed_type_name(seed)  # NB: seed_type_name here may also auto-detect 'bip39'
        if self.seed_type == 'bip39':
            f=lambda passphrase: self.on_restore_bip39(seed, passphrase)
            self.passphrase_dialog(run_next=f) if is_ext else f('')
        elif self.seed_type in ['standard', 'electrum']:
            f = lambda passphrase: self.run('create_keystore', seed, passphrase)
            self.passphrase_dialog(run_next=f) if is_ext else f('')
        elif self.seed_type == 'old':
            self.run('create_keystore', seed, '')
        else:
            raise BaseException('Unknown seed type', self.seed_type)

    def on_restore_bip39(self, seed, passphrase):
        f = lambda x: self.run('on_bip44', seed, passphrase, str(x))
        self.derivation_dialog(f, keystore.bip44_derivation_xec(0), seed=seed)

    def create_keystore(self, seed, passphrase):
        # auto-detect, prefers old, electrum, bip39 in that order. Since we
        # never create ambiguous seeds, this is fine.
        k = keystore.from_seed(seed, passphrase)
        self.on_keystore(k)

    def on_bip44(self, seed, passphrase, derivation):
        # BIP39
        k = keystore.from_seed(seed, passphrase, derivation=derivation, seed_type='bip39')
        self.on_keystore(k)

    def on_keystore(self, k):
        has_xpub = isinstance(k, keystore.Xpub)
        if has_xpub:
            from .bitcoin import xpub_type
            t1 = xpub_type(k.xpub)
        if self.wallet_type == 'standard':
            if has_xpub and t1 not in ['standard']:
                self.show_error(_('Wrong key type') + ' %s'%t1)
                self.run('choose_keystore')
                return
            self.keystores.append(k)
            self.run('create_wallet')
        elif self.wallet_type == 'multisig':
            assert has_xpub
            if t1 not in ['standard']:
                self.show_error(_('Wrong key type') + ' %s'%t1)
                self.run('choose_keystore')
                return
            if k.xpub in map(lambda x: x.xpub, self.keystores):
                self.show_error(_('Error: duplicate master public key'))
                self.run('choose_keystore')
                return
            if len(self.keystores)>0:
                t2 = xpub_type(self.keystores[0].xpub)
                if t1 != t2:
                    self.show_error(_('Cannot add this cosigner:') + '\n' + "Their key type is '%s', we are '%s'"%(t1, t2))
                    self.run('choose_keystore')
                    return
            self.keystores.append(k)
            if len(self.keystores) == 1:
                xpub = k.get_master_public_key()
                self.stack = []
                self.run('show_xpub_and_add_cosigners', xpub)
            elif len(self.keystores) < self.n:
                self.run('choose_keystore')
            else:
                self.run('create_wallet')

    def create_wallet(self):
        encrypt_keystore = any(k.may_have_password() for k in self.keystores)
        # note: the following condition ("if") is duplicated logic from
        # wallet.get_available_storage_encryption_version()
        if self.wallet_type == 'standard' and isinstance(self.keystores[0], keystore.Hardware_KeyStore):
            # offer encrypting with a pw derived from the hw device
            k = self.keystores[0]
            try:
                k.handler = self.plugin.create_handler(self)
                password = k.get_password_for_storage_encryption()
            except util.UserCancelled:
                devmgr = self.plugins.device_manager
                devmgr.unpair_xpub(k.xpub)
                self.choose_hw_device()
                return
            except BaseException as e:
                traceback.print_exc(file=sys.stderr)
                self.show_error(str(e))
                return
            self.request_storage_encryption(
                run_next=lambda encrypt_storage: self.on_password(
                    password,
                    encrypt_storage=encrypt_storage,
                    storage_enc_version=STO_EV_XPUB_PW,
                    encrypt_keystore=False))
        else:
            # prompt the user to set an arbitrary password
            self.request_password(
                run_next=lambda password, encrypt_storage: self.on_password(
                    password,
                    encrypt_storage=encrypt_storage,
                    storage_enc_version=STO_EV_USER_PW,
                    encrypt_keystore=encrypt_keystore),
                force_disable_encrypt_cb=not encrypt_keystore)

    def on_password(self, password, *, encrypt_storage,
                    storage_enc_version=STO_EV_USER_PW, encrypt_keystore):
        self.storage.set_keystore_encryption(bool(password) and encrypt_keystore)
        if encrypt_storage:
            self.storage.set_password(password, enc_version=storage_enc_version)
        for k in self.keystores:
            if k.may_have_password():
                k.update_password(None, password)
        if self.wallet_type == 'standard':
            self.storage.put('seed_type', self.seed_type)
            keys = self.keystores[0].dump()
            self.storage.put('keystore', keys)
            self.wallet = Standard_Wallet(self.storage)
            self.run('create_addresses')
        elif self.wallet_type == 'multisig':
            for i, k in enumerate(self.keystores):
                self.storage.put('x%d/'%(i+1), k.dump())
            self.storage.write()
            self.wallet = Multisig_Wallet(self.storage)
            self.run('create_addresses')
        elif self.wallet_type == 'imported_addr':
            self.wallet = ImportedAddressWallet(self.storage)
            self.storage.write()
            self.terminate()
        elif self.wallet_type == 'imported_privkey':
            if len(self.keystores) > 0:
                keys = self.keystores[0].dump()
                self.storage.put('keystore', keys)
            self.wallet = ImportedPrivkeyWallet(self.storage)
            self.wallet.storage.write()
            self.wallet.save_keystore()

    def show_xpub_and_add_cosigners(self, xpub):
        self.show_xpub_dialog(xpub=xpub, run_next=lambda x: self.run('choose_keystore'))

    def on_cosigner(self, text, password, i):
        k = keystore.from_master_key(text, password)
        self.on_keystore(k)

    def create_standard_seed(self):
        # we now generate bip39 by default; changing the below back to
        # 'electrum' would default to electrum seeds again.
        self.create_seed('bip39')

    def create_seed(self, seed_type):
        from . import mnemo
        self.seed_type = seed_type
        if seed_type in ['standard', 'electrum']:
            seed = mnemo.Mnemonic_Electrum('en').make_seed()
        elif seed_type == 'bip39':
            seed = mnemo.make_bip39_words('english')
        else:
            # This should never happen.
            raise ValueError('Cannot make seed for unknown seed type ' + str(seed_type))
        self.opt_bip39 = False
        f = lambda x: self.request_passphrase(seed, x)
        self.show_seed_dialog(run_next=f, seed_text=seed)

    def request_passphrase(self, seed, opt_passphrase):
        if opt_passphrase:
            f = lambda x: self.confirm_seed(seed, x)
            self.passphrase_dialog(run_next=f)
        else:
            self.run('confirm_seed', seed, '')

    def confirm_seed(self, seed, passphrase):
        f = lambda x: self.confirm_passphrase(seed, passphrase)
        self.confirm_seed_dialog(run_next=f, test=lambda x: x==seed)

    def confirm_passphrase(self, seed, passphrase):
        f = lambda x: self.run('create_keystore', seed, x)
        if passphrase:
            title = _('Confirm Seed Extension')
            message = '\n'.join([
                _('Your seed extension must be saved together with your seed.'),
                _('Please type it here.'),
            ])
            self.line_dialog(run_next=f, title=title, message=message, default='', test=lambda x: x==passphrase)
        else:
            f('')

    def create_addresses(self):
        def task():
            self.wallet.synchronize()
            self.wallet.storage.write()
            self.terminate()
        msg = _(f"{PROJECT_NAME} is generating your addresses, please wait.")
        self.waiting_dialog(task, msg)
