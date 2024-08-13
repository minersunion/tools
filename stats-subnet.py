import argparse
import traceback
from datetime import timedelta

import bittensor
from bittensor import SubnetInfo
import pandas as pd


def looking_for_index(_list, _value):
    for index, element in enumerate(_list):
        if element[0] == _value:
            return index
    return -1


def prettify_time(seconds):
    delta = timedelta(seconds=seconds)
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    time_str = f"{days:02}d:{hours:02}h:{minutes:02}m"
    return time_str


def calculate_widths(df):
    return {col: max(len(col), df[col].astype(str).str.len().max()) + 2 for col in df.columns}


def left_align_formatter(width):
    return lambda x: str(x).ljust(width)


def get_info(config):
    print(f"Subnet: {config.netuid}")

    coldkeys, _ = bittensor.commands.wallets._get_coldkey_ss58_addresses_for_path(config.wallet.path)

    weights: bool = config.weights
    subtensor = bittensor.subtensor(config=config, network=config.chain_endpoint, log_verbose=False)

    subnet_info: SubnetInfo = subtensor.get_subnet_info(config.netuid)
    metagraph: bittensor.metagraph = subtensor.metagraph(config.netuid)
    current_block = subtensor.get_current_block()
    uids = metagraph.uids.tolist()

    unique_ip_addresses = set()

    curr_block = metagraph.block

    uids_to_check = []
    personal_scores = {}

    if weights:
        print(f"{'uid':<10}{'weights':<15}")
        metagraph.sync(lite=False)
        for uid in uids:
            neuron: bittensor.NeuronInfo = metagraph.neurons[uid]
            balance: bittensor.Balance = neuron.total_stake

            if neuron.coldkey:
                uids_to_check.append(uid)

            if balance.tao > 1_000:
                print(f"{uid:<10}{curr_block - neuron.last_update:<15}{neuron.weights}")

            for uid_to_check in uids_to_check:
                index = looking_for_index(neuron.weights, uid_to_check)
                if index >= 0:
                    if uid_to_check not in personal_scores:
                        personal_scores[uid_to_check] = []
                    personal_scores[uid_to_check].append((neuron.weights[index][1], uid))

        for uid_to_check in uids_to_check:
            personal_scores[uid_to_check] = sorted(personal_scores.get(uid_to_check, []), key=lambda x: x[0], reverse=True)

        for uid, values in personal_scores.items():
            print(f"Scores for uid: {uid}")
            for value in values:
                print(value)

    # Collect data into lists
    validators_stats = []
    miners_stats = []

    for uid in uids:
        neuron: bittensor.NeuronInfo = metagraph.neurons[uid]

        axon = neuron.axon_info
        ip_address = axon.ip
        port = axon.port
        stake: bittensor.Balance = neuron.total_stake
        last_update: int = neuron.last_update
        calc_last_update: int = curr_block - last_update
        full_address = f"{ip_address}:{port}"

        emission = metagraph.E[uid]
        trust = metagraph.trust[uid]
        vtrust = metagraph.validator_trust[uid]
        is_validator = stake.tao > 1_024
        mine = "✅" if axon.coldkey in coldkeys else "❌"

        block_at_registration = int(str(subtensor.query_subtensor("BlockAtRegistration", None, [config.netuid, uid])))
        since_reg: str = prettify_time((current_block - block_at_registration) * bittensor.__blocktime__)
        immune = block_at_registration + subnet_info.immunity_period > current_block
        immune = "✅" if immune else "❌"

        if config.long_key:
            pretty_hotkey = axon.hotkey
        else:
            pretty_hotkey = axon.hotkey[:12]

        if config.long_key:
            pretty_coldkey = axon.coldkey
        else:
            pretty_coldkey = axon.coldkey[:12]

        stats = {
            "full_address": full_address,
            "uid": uid,
            "axon": axon.version,
            # "prometheus": neuron.prometheus_info.version,
            "last_update": calc_last_update,
            "stake": stake.tao,
            "emission": emission,
            "trust": trust,
            "vtrust": vtrust,
            "coldkey": pretty_coldkey,
            "hotkey": pretty_hotkey,
            "since_reg": since_reg,
            "mine": mine,
            "immune": immune,
            "duplicate_ip": "✅" if ip_address in unique_ip_addresses else "❌",
        }

        if is_validator:
            validators_stats.append(stats)
        else:
            miners_stats.append(stats)

        unique_ip_addresses.add(ip_address)

    # Convert lists to DataFrames
    validators_df = pd.DataFrame(validators_stats)
    miners_df = pd.DataFrame(miners_stats)

    # round to config decimals
    validators_df = validators_df.round(config.round)
    miners_df = miners_df.round(config.round)

    # Sorting
    sort_keys = ["emission", "trust"] if config.sort == "emission" else ["trust", "emission"]
    validators_df = validators_df.sort_values(by=sort_keys, ascending=False)
    miners_df = miners_df.sort_values(by=sort_keys, ascending=False)

    # Add rank column based on index after sorting
    validators_df = validators_df.reset_index(drop=True)
    miners_df = miners_df.reset_index(drop=True)
    validators_df["rank"] = validators_df.index + 1
    miners_df["rank"] = miners_df.index + 1

    # Reorder columns to have "rank" first
    columns_order = ["rank"] + [col for col in validators_df.columns if col != "rank"]
    validators_df = validators_df[columns_order]
    miners_df = miners_df[columns_order]

    # Create formatters for each column to align left
    validators_widths = calculate_widths(validators_df)
    miners_widths = calculate_widths(miners_df)
    formatters_validators = {col: left_align_formatter(validators_widths[col]) for col in validators_df.columns}
    formatters_miners = {col: left_align_formatter(miners_widths[col]) for col in miners_df.columns}

    # Display Validators with left-aligned columns
    print("\nValidators:\n")
    print(validators_df.to_string(formatters=formatters_validators, justify="left", index=False))

    # Display Miners with left-aligned columns
    print("\nMiners:\n")
    print(miners_df.to_string(formatters=formatters_miners, justify="left", index=False))

    # Summary statistics
    print()
    print(f"{'[Validators] Active':<40}{len(validators_df)}")
    print(f"{'[Miners]     Active':<40}{len(miners_df)}")
    print(f"{'[Validators] Emissions ~/epoch':<40}{validators_df['emission'].sum()}")
    print(f"{'[Miners]     Emissions ~/epoch':<40}{miners_df['emission'].sum()}")
    print(f"{'[Validators] Emissions ~/day':<40}{validators_df['emission'].sum() * 20}")
    print(f"{'[Miners]     Emissions ~/day':<40}{miners_df['emission'].sum() * 20}")


def main(config):
    try:
        get_info(config)
    except Exception as e:
        bittensor.logging.error(e)
        traceback.print_exc()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--netuid", type=int, default=0, help="The chain subnet uid.")
    parser.add_argument("--weights", action="store_true", help="Show the validator weights.")
    parser.add_argument("--long_key", action="store_true", help="Show the full hot and cold keys.")
    parser.add_argument("--sort", type=str, default="emission")  # TODO allow more sorting rather than emission or trust
    parser.add_argument("--round", type=str, default=5)

    bittensor.subtensor.add_args(parser)
    bittensor.logging.add_args(parser)
    bittensor.wallet.add_args(parser)
    config = bittensor.config(parser)
    main(config)
