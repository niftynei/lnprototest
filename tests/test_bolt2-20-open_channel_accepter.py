#! /usr/bin/env python3
# Variations on open_channel, accepter perspective

from hashlib import sha256
from lnprototest import TryAll, Connect, Block, FundChannel, ExpectMsg, ExpectTx, Msg, RawMsg, KeySet, AcceptFunding, CreateFunding, Commit, Runner, remote_funding_pubkey, remote_revocation_basepoint, remote_payment_basepoint, remote_htlc_basepoint, remote_per_commitment_point, remote_delayed_payment_basepoint, Side, CheckEq, msat, remote_funding_privkey, regtest_hash, bitfield, Event, DualFundAccept, OneOf, CreateDualFunding, EventError, Funding, privkey_expand, AddInput, AddOutput, FinalizeFunding, AddWitnesses
from lnprototest.stash import sent, rcvd, commitsig_to_send, commitsig_to_recv, channel_id, funding_txid, funding_tx, funding, locking_script, get_member
from helpers import utxo, tx_spendable, funding_amount_for_utxo, pubkey_of, tx_out_for_index, privkey_for_index
from typing import Callable
import coincurve
import functools
import pyln.spec.bolt2


def channel_id_v2(local_keyset: KeySet) -> Callable[[Runner, Event, str], str]:

    def _channel_id_v2(runner: Runner, event: Event, field: str) -> str:

        # BOLT-0eebb43e32a513f3b4dd9ced72ad1e915aefdd25 #2:
        #
        # For channels established using the v2 protocol, the `channel_id` is the
        # SHA256(lesser-revocation-basepoint || greater-revocation-basepoint),
        # where the lesser and greater is based off the order of the
        # basepoint. The basepoints are compact DER-encoded public keys.
        remote_key = runner.get_keyset().raw_revocation_basepoint()
        local_key = local_keyset.raw_revocation_basepoint()
        if remote_key.format() < local_key.format():
            return sha256(remote_key.format() + local_key.format()).digest().hex()
        else:
            return sha256(local_key.format() + remote_key.format()).digest().hex()
    return _channel_id_v2


def channel_id_tmp(local_keyset: KeySet, opener: Side) -> Callable[[Runner, Event, str], str]:
    def _channel_id_tmp(runner: Runner, event: Event, field: str) -> str:
        # BOLT-f53ca2301232db780843e894f55d95d512f297f9 #2:
        #
        # If the peer's revocation basepoint is unknown (e.g. `open_channel2`),
        # a temporary `channel_id` should be found by using a zeroed out
        # basepoint for the unknown peer.
        if opener == Side.local:
            key = local_keyset.raw_revocation_basepoint()
        else:
            key = runner.get_keyset().raw_revocation_basepoint()

        return sha256(bytes.fromhex('00' * 33) + key.format()).digest().hex()

    return _channel_id_tmp


def test_open_accepter_channel(runner: Runner) -> None:
    # Needs modified spec, so don't even try unless we have that!
    if not 'open_channel2' in pyln.spec.bolt2.__dict__:
        return

    local_funding_privkey = '20'

    local_keyset = KeySet(revocation_base_secret='21',
                          payment_base_secret='22',
                          htlc_base_secret='24',
                          delayed_payment_base_secret='23',
                          shachain_seed='00' * 32)

    input_index = 0

    test = [Block(blockheight=102, txs=[tx_spendable]),
            Connect(connprivkey='02'),
            ExpectMsg('init'),

            # BOLT-ff9a3470f5a0f475dc0909bf153620a73ca7b21e #9:
            # | 222/223 | `option_dual_fund`          | Use v2 channel open
            Msg('init', globalfeatures='', features=bitfield(12, 20, 223)),

            # Accepter side: we initiate a new channel.
            Msg('open_channel2',
                chain_hash=regtest_hash,
                funding_satoshis=funding_amount_for_utxo(input_index),
                dust_limit_satoshis=546,
                max_htlc_value_in_flight_msat=4294967295,
                htlc_minimum_msat=0,
                feerate_per_kw=253,
                feerate_per_kw_funding=253,
                # We use 5, because c-lightning runner uses 6, so this is different.
                to_self_delay=5,
                max_accepted_htlcs=483,
                locktime=0,
                podle_h2='00' * 32,
                funding_pubkey=pubkey_of(local_funding_privkey),
                revocation_basepoint=local_keyset.revocation_basepoint(),
                payment_basepoint=local_keyset.payment_basepoint(),
                delayed_payment_basepoint=local_keyset.delayed_payment_basepoint(),
                htlc_basepoint=local_keyset.htlc_basepoint(),
                first_per_commitment_point=local_keyset.per_commit_point(0),
                channel_flags=1),

            # Ignore unknown odd messages
            TryAll([], RawMsg(bytes.fromhex('270F'))),

            ExpectMsg('accept_channel2',
                      channel_id=channel_id_v2(local_keyset),
                      funding_satoshis=0,
                      funding_pubkey=remote_funding_pubkey(),
                      revocation_basepoint=remote_revocation_basepoint(),
                      payment_basepoint=remote_payment_basepoint(),
                      delayed_payment_basepoint=remote_delayed_payment_basepoint(),
                      htlc_basepoint=remote_htlc_basepoint(),
                      first_per_commitment_point=remote_per_commitment_point(0)),

            # Ignore unknown odd messages
            TryAll([], RawMsg(bytes.fromhex('270F'))),

            # Create and stash Funding object and FundingTx
            CreateFunding(*utxo(input_index),
                          local_node_privkey='02',
                          local_funding_privkey=local_funding_privkey,
                          remote_node_privkey=runner.get_node_privkey(),
                          remote_funding_privkey=remote_funding_privkey()),

            Commit(funding=funding(),
                   opener=Side.local,
                   local_keyset=local_keyset,
                   local_to_self_delay=rcvd('accept_channel2.to_self_delay', int),
                   remote_to_self_delay=sent('open_channel2.to_self_delay', int),
                   local_amount=msat(sent('open_channel2.funding_satoshis', int)),
                   remote_amount=0,
                   local_dust_limit=546,
                   remote_dust_limit=546,
                   feerate=253,
                   local_features=sent('init.features'),
                   remote_features=rcvd('init.features')),

            Msg('tx_add_input',
                channel_id=rcvd('accept_channel2.channel_id'),
                serial_id=2,
                prevtx=tx_spendable,
                prevtx_vout=tx_out_for_index(input_index),
                sequence=0xfffffffd,
                script=''),

           # Ignore unknown odd messages
           TryAll([], RawMsg(bytes.fromhex('270F'))),

           ExpectMsg('tx_complete',
                     channel_id=rcvd('accept_channel2.channel_id')),

           Msg('tx_add_output',
               channel_id=rcvd('accept_channel2.channel_id'),
               serial_id=2,
               sats=funding_amount_for_utxo(input_index),
               script=locking_script()),

           # Ignore unknown odd messages
           TryAll([], RawMsg(bytes.fromhex('270F'))),

           ExpectMsg('tx_complete',
                     channel_id=rcvd('accept_channel2.channel_id')),

           Msg('tx_complete',
               channel_id=rcvd('accept_channel2.channel_id')),

           # Ignore unknown odd messages
           TryAll([], RawMsg(bytes.fromhex('270F'))),

           Msg('commitment_signed',
               channel_id=rcvd('accept_channel2.channel_id'),
               signature=commitsig_to_send(),
               htlc_signature='[]'),

           # Ignore unknown odd messages
           TryAll([], RawMsg(bytes.fromhex('270F'))),

           ExpectMsg('commitment_signed',
                     channel_id=rcvd('accept_channel2.channel_id'),
                     signature=commitsig_to_recv()),

           ExpectMsg('tx_signatures',
                     channel_id=rcvd('accept_channel2.channel_id'),
                     txid=funding_txid(),
                     witness_stack='[]'),

           # Mine the block!
           Block(blockheight=103, number=3, txs=[funding_tx()]),

           Msg('funding_locked',
               channel_id=rcvd('accept_channel2.channel_id'),
               next_per_commitment_point='027eed8389cf8eb715d73111b73d94d2c2d04bf96dc43dfd5b0970d80b3617009d'),

           ExpectMsg('funding_locked',
                     channel_id=rcvd('accept_channel2.channel_id'),
                     next_per_commitment_point='032405cbd0f41225d5f203fe4adac8401321a9e05767c5f8af97d51d2e81fbb206'),

           # Ignore unknown odd messages
           TryAll([], RawMsg(bytes.fromhex('270F')))
        ]

    runner.run(test)


def odd_serial(event: Event, msg: Msg) -> None:
    if msg.fields['serial_id'] % 2 == 0:
        raise EventError(event, "Received **even** serial {}, expected odd".format(msg.fields['serial_id']))


def agreed_funding() -> Callable[[Runner, Event, str], int]:
    def _agreed_funding(runner: Runner, event: Event, field: str) -> int:
        open_funding = get_member(event, runner, 'Msg', 'open_channel2.funding_satoshis')
        accept_funding = get_member(event, runner, 'ExpectMsg', 'accept_channel2.funding_satoshis')
        return open_funding + accept_funding
    return _agreed_funding


def funding_lockscript(our_privkey: str) -> Callable[[Runner, Event, str], str]:
    def _funding_lockscript(runner: Runner, event: Event, field: str) -> str:
        remote_pubkey = Funding.funding_pubkey_key(privkey_expand(runner.get_node_bitcoinkey()))
        local_pubkey = Funding.funding_pubkey_key(privkey_expand(our_privkey))
        return Funding.locking_script_keys(remote_pubkey, local_pubkey).hex()
    return _funding_lockscript


def test_open_dual_accepter_channel(runner: Runner) -> None:
    # Needs modified spec, so don't even try unless we have that!
    if not 'open_channel2' in pyln.spec.bolt2.__dict__:
        return

    local_funding_privkey = '20'

    local_keyset = KeySet(revocation_base_secret='21',
                          payment_base_secret='22',
                          htlc_base_secret='24',
                          delayed_payment_base_secret='23',
                          shachain_seed='00' * 32)

    input_index = 5

    # Since technically these can be sent in any order,
    # we must specify this as ok!
    expected_add_input = ExpectMsg('tx_add_input',
        channel_id=rcvd('accept_channel2.channel_id'),
        sequence=0xfffffffd,
        script='',
        if_match=odd_serial)

    expected_add_output = ExpectMsg('tx_add_output',
        channel_id=rcvd('accept_channel2.channel_id'),
        if_match=odd_serial)

    test = [Block(blockheight=102, txs=[tx_spendable]),
            Connect(connprivkey='02'),
            ExpectMsg('init'),

            # BOLT-ff9a3470f5a0f475dc0909bf153620a73ca7b21e #9:
            # | 222/223 | `option_dual_fund`          | Use v2 channel open
            Msg('init', globalfeatures='', features=bitfield(12, 20, 223)),

            DualFundAccept(),

            # Accepter side: we initiate a new channel.
            Msg('open_channel2',
                chain_hash=regtest_hash,
                funding_satoshis=funding_amount_for_utxo(input_index),
                dust_limit_satoshis=546,
                max_htlc_value_in_flight_msat=4294967295,
                htlc_minimum_msat=0,
                feerate_per_kw=253,
                feerate_per_kw_funding=253,
                # We use 5, because c-lightning runner uses 6, so this is different.
                to_self_delay=5,
                max_accepted_htlcs=483,
                locktime=100,
                podle_h2='00' * 32,
                funding_pubkey=pubkey_of(local_funding_privkey),
                revocation_basepoint=local_keyset.revocation_basepoint(),
                payment_basepoint=local_keyset.payment_basepoint(),
                delayed_payment_basepoint=local_keyset.delayed_payment_basepoint(),
                htlc_basepoint=local_keyset.htlc_basepoint(),
                first_per_commitment_point=local_keyset.per_commit_point(0),
                channel_flags=1),

            # Ignore unknown odd messages
            TryAll([], RawMsg(bytes.fromhex('270F'))),

            ExpectMsg('accept_channel2',
                      channel_id=channel_id_v2(local_keyset),
                      funding_satoshis=funding_amount_for_utxo(input_index),
                      funding_pubkey=remote_funding_pubkey(),
                      revocation_basepoint=remote_revocation_basepoint(),
                      payment_basepoint=remote_payment_basepoint(),
                      delayed_payment_basepoint=remote_delayed_payment_basepoint(),
                      htlc_basepoint=remote_htlc_basepoint(),
                      first_per_commitment_point=remote_per_commitment_point(0)),

            # Create and stash Funding object and FundingTx
            CreateDualFunding(*utxo(input_index),
                              funding_sats=agreed_funding(),
                              locktime=sent('open_channel2.locktime', int),
                              local_node_privkey='02',
                              local_funding_privkey=local_funding_privkey,
                              remote_node_privkey=runner.get_node_privkey(),
                              remote_funding_privkey=remote_funding_privkey()),

            # Ignore unknown odd messages
            TryAll([], RawMsg(bytes.fromhex('270F'))),

            Msg('tx_add_input',
                channel_id=rcvd('accept_channel2.channel_id'),
                serial_id=0,
                sequence=0xfffffffd,
                prevtx=tx_spendable,
                prevtx_vout=tx_out_for_index(input_index),
                script=''),

            AddInput(funding=funding(),
                     privkey=privkey_for_index(input_index),
                     serial_id=sent('tx_add_input.serial_id', int),
                     prevtx=sent(),
                     prevtx_vout=sent('tx_add_input.prevtx_vout', int),
                     script=sent()),

            OneOf([expected_add_input,
                  Msg('tx_add_output',
                   channel_id=rcvd('accept_channel2.channel_id'),
                   serial_id=0,
                   sats=agreed_funding(),
                   script=funding_lockscript(local_funding_privkey)),
                 expected_add_output],
                [expected_add_output,
                 Msg('tx_add_output',
                   channel_id=rcvd('accept_channel2.channel_id'),
                   serial_id=2,
                   sats=agreed_funding(),
                   script=funding_lockscript(local_funding_privkey)),
                 expected_add_input]),

           AddInput(funding=funding(),
                    serial_id=rcvd('tx_add_input.serial_id', int),
                    prevtx=rcvd('tx_add_input.prevtx'),
                    prevtx_vout=rcvd('tx_add_input.prevtx_vout', int),
                    script=rcvd('tx_add_input.script')),

           AddOutput(funding=funding(),
                     serial_id=rcvd('tx_add_output.serial_id', int),
                     sats=rcvd('tx_add_output.sats', int),
                     script=rcvd('tx_add_output.script')),

           AddOutput(funding=funding(),
                     serial_id=sent('tx_add_output.serial_id', int),
                     sats=sent('tx_add_output.sats', int),
                     script=sent('tx_add_output.script')),

           FinalizeFunding(funding=funding()),

           # Ignore unknown odd messages
           TryAll([], RawMsg(bytes.fromhex('270F'))),

           Msg('tx_complete',
               channel_id=rcvd('accept_channel2.channel_id')),

           ExpectMsg('tx_complete',
                     channel_id=rcvd('accept_channel2.channel_id')),


            Commit(funding=funding(),
                   opener=Side.local,
                   local_keyset=local_keyset,
                   local_to_self_delay=rcvd('accept_channel2.to_self_delay', int),
                   remote_to_self_delay=sent('open_channel2.to_self_delay', int),
                   local_amount=msat(sent('open_channel2.funding_satoshis', int)),
                   remote_amount=msat(rcvd('accept_channel2.funding_satoshis', int)),
                   local_dust_limit=546,
                   remote_dust_limit=546,
                   feerate=253,
                   local_features=sent('init.features'),
                   remote_features=rcvd('init.features')),

           # Ignore unknown odd messages
           TryAll([], RawMsg(bytes.fromhex('270F'))),

           Msg('commitment_signed',
               channel_id=rcvd('accept_channel2.channel_id'),
               signature=commitsig_to_send(),
               htlc_signature='[]'),

           # Ignore unknown odd messages
           TryAll([], RawMsg(bytes.fromhex('270F'))),

           ExpectMsg('commitment_signed',
                     channel_id=rcvd('accept_channel2.channel_id'),
                     signature=commitsig_to_recv()),

           ExpectMsg('tx_signatures',
                     channel_id=rcvd('accept_channel2.channel_id'),
                     txid=funding_txid()),

           AddWitnesses(funding=funding(),
                        witness_stack=rcvd('witness_stack')),

           # Mine the block!
           Block(blockheight=103, number=3, txs=[funding_tx()]),

           Msg('funding_locked',
               channel_id=rcvd('accept_channel2.channel_id'),
               next_per_commitment_point=local_keyset.per_commit_point(1)),

           ExpectMsg('funding_locked',
                     channel_id=rcvd('accept_channel2.channel_id'),
                     next_per_commitment_point=remote_per_commitment_point(1)),

           # Ignore unknown odd messages
           TryAll([], RawMsg(bytes.fromhex('270F')))
        ]

    runner.run(test)
