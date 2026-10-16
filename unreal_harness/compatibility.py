"""Reject known incompatible runtimes before model planning or mutations."""
import re

from .action_contract import CONTRACT_VERSION


def validate_runtime(capabilities):
    if not isinstance(capabilities, dict):
        raise ValueError("Invalid runtime capabilities")
    engine = capabilities.get("engine_version")
    if engine is not None and (not isinstance(engine, str) or not re.match(r"^5\.8(?:[.\-]|$)", engine)):
        raise ValueError("Unsupported Unreal runtime: {}; expected 5.8".format(engine))
    contract = capabilities.get("contract_version")
    if contract is not None and contract != CONTRACT_VERSION:
        raise ValueError("Runtime contract {} differs from host {}; update the installed runtime".format(contract, CONTRACT_VERSION))
