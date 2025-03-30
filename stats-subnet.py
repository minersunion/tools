import argparse
import traceback
from datetime import timedelta

import bittensor
import bittensor_cli
import requests
from bittensor import SubnetInfo, BLOCKTIME, MetagraphInfoPool, ChainIdentity
import pandas as pd
from rich.console import Console
from rich.table import Table

console = Console()


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


def display_table(title, df):
    table = Table(title=title)

    for col in df.columns:
        table.add_column(col, justify="left")

    for _, row in df.iterrows():
        table.add_row(*map(str, row))

    console.print(table)


def get_tao_price_usd() -> float:
    tao_price_url = "https://hermes.pyth.network/v2/updates/price/latest?ids%5B%5D=0x410f41de235f2db824e562ea7ab2d3d3d4ff048316c61d629c0b93f58584e1af"

    response = requests.get(tao_price_url)
    if response.status_code != 200:
        print(f"Failed to get TAO price: {response.text}")
        return 0

    data = response.json()
    parsed_item = data["parsed"][0]
    price = parsed_item["price"]
    raw_price = float(price["price"])
    expo = int(price["expo"])
    tao_price = raw_price * (10**expo)
    return tao_price


def get_info(config):
    print(f"Subnet: {config.netuid}")

    coldkeys, _ = bittensor_cli.cli.wallets._get_coldkey_ss58_addresses_for_path(config.wallet.path)

    weights: bool = config.weights
    subtensor = bittensor.subtensor(config=config, network=config.chain_endpoint, log_verbose=False)

    identities: dict[str, ChainIdentity] = subtensor.get_delegate_identities()

    subnet_infos: list[SubnetInfo] = subtensor.get_all_subnets_info()
    subnet_info = [subnet_info for subnet_info in subnet_infos if subnet_info.netuid == config.netuid][0]

    metagraph: bittensor.metagraph = subtensor.metagraph(config.netuid)
    current_block = subtensor.get_current_block()
    uids = metagraph.uids.tolist()

    tempo_blocks: int = metagraph.tempo
    tempo_seconds: int = tempo_blocks * BLOCKTIME
    seconds_in_day: int = 60 * 60 * 24
    tempos_per_day: int = int(seconds_in_day / tempo_seconds)

    pool: MetagraphInfoPool = metagraph.pool
    alpha_token_price: float = pool.tao_in / pool.alpha_in

    unique_ip_addresses = set()

    curr_block = metagraph.block

    uids_to_check = []
    personal_scores = {}

    tao_price = get_tao_price_usd()

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

        emission = float(metagraph.E[uid])
        trust = float(metagraph.trust[uid])
        vtrust = float(metagraph.validator_trust[uid])
        is_validator = vtrust > 0.01
        mine = "MINE" if axon.coldkey in coldkeys else "-"

        block_at_registration = subtensor.query_subtensor("BlockAtRegistration", None, [config.netuid, uid])
        block_at_registration = int(block_at_registration.value)
        since_reg: str = prettify_time((current_block - block_at_registration) * bittensor.BLOCKTIME)
        immune = block_at_registration + subnet_info.immunity_period > current_block
        immune = "✅" if immune else "❌"

        daily_rewards_alpha: float = float(tempos_per_day * emission)
        daily_rewards_tao: float = daily_rewards_alpha * alpha_token_price

        if config.hot_key:
            pretty_hotkey = axon.hotkey
        else:
            pretty_hotkey = axon.hotkey[:12]

        if config.cold_key:
            pretty_coldkey = axon.coldkey
        else:
            pretty_coldkey = axon.coldkey[:12]

        pretty_coldkey = identities.get(axon.coldkey).name if axon.coldkey in identities else pretty_coldkey

        stats = {
            "address": full_address,
            "uid": uid,
            "axon": axon.version,
            "last upd.": calc_last_update,
            "stake": stake.tao,
            "emission": emission or 0,
            "alpha/d": daily_rewards_alpha,
            "tao/d": daily_rewards_tao,
            "$/d": daily_rewards_tao * tao_price,
            "trust": trust,
            "vtrust": vtrust,
            "coldkey": pretty_coldkey,
            "hotkey": pretty_hotkey,
            "reg since": since_reg,
            "mine": mine,
            "immune": immune,
            "dupl. ip": "✅" if ip_address in unique_ip_addresses else "❌",
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

    # Add P. column based on index after sorting
    validators_df = validators_df.reset_index(drop=True)
    miners_df = miners_df.reset_index(drop=True)
    validators_df["P."] = validators_df.index + 1
    miners_df["P."] = miners_df.index + 1

    # Reorder columns to have "P." first
    columns_order = ["P."] + [col for col in validators_df.columns if col != "P."]
    validators_df = validators_df[columns_order]
    miners_df = miners_df[columns_order]

    display_table("Validators", validators_df)
    display_table("Miners", miners_df)

    # Summary statistics
    print()
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
    parser.add_argument("--hot-key", dest="hot_key", action="store_true", help="Show the full hot key.")
    parser.add_argument("--cold-key", dest="cold_key", action="store_true", help="Show the full cold key.")
    parser.add_argument("--sort", type=str, default="emission")  # TODO allow more sorting rather than emission or trust
    parser.add_argument("--round", type=str, default=3)

    bittensor.subtensor.add_args(parser)
    bittensor.logging.add_args(parser)
    bittensor.wallet.add_args(parser)
    config = bittensor.config(parser)
    main(config)
