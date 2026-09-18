"""Canonical, read-only contracts for controller scheduler actions."""
from __future__ import annotations

from dataclasses import asdict, dataclass


class ActionContractError(ValueError):
    def __init__(self, code, details=None):
        self.code, self.details = code, details or {}
        super().__init__(code)


@dataclass(frozen=True)
class ActionContract:
    name: str; phase: str; inputs: tuple[str, ...]; preconditions: tuple[str, ...]
    authorization: str; evidence: tuple[str, ...]; side_effects: tuple[str, ...]
    output_state: str; failure_class: str; recovery: str; idempotency: str; references: tuple[str, ...]; guidance: str

    def public(self): return asdict(self)


_PHASE_ACTIONS = {
    "preflight": ("run_preflight", "run_grill", "run_planning", "confirm_no_change", "enter_implementation"),
    "implementation": ("publish_spec", "ticket_current_spec", "dispatch_spec", "wait_spec", "wait_spec_dependency", "wait_ticket_receipt", "verify_spec", "merge_spec", "close_spec", "repair_spec", "advance_spec", "advance_ticket", "dispatch_ticket", "wait_ticket", "wait_ticket_blocker", "verify_ticket", "merge_ticket", "close_ticket", "repair_ticket", "repair_queue", "reconcile_intent", "verify_thread_route", "assign_thread", "run_checkpoint"),
    "release": ("final_verification", "advance_release", "advance_synchronization", "complete_run", "final_release", "terminal", "repair_run"),
}
_READ_ONLY = {"wait_spec", "wait_ticket", "wait_ticket_blocker", "verify_spec", "verify_ticket", "terminal"}
_RECOVERY = {"repair_spec", "repair_ticket", "repair_queue"}


def _contract(name, phase):
    side_effects = () if name in _READ_ONLY else ("controller_state",)
    if name.startswith("dispatch") or name.startswith("merge") or name == "final_release":
        side_effects = ("controller_state", "external_task_or_repository")
    guidance = f"Execute {name} only after its declared preconditions and authorization pass; persist verified readback, or classify the failure and follow the declared recovery policy."
    return ActionContract(name, phase, ("run_id", "target"), ("fresh_business_version",), "configured_authorization", ("verified_readback",), side_effects, "recorded_next_action", "blocked_or_inconclusive", "reconcile_and_retry_with_budget", f"{name}:run_id:target", ("references/spec-delivery.md", "references/external-actions.md"), guidance)


ACTION_CONTRACTS = {name: _contract(name, phase) for phase, names in _PHASE_ACTIONS.items() for name in names}


def validate_contracts(contracts=ACTION_CONTRACTS):
    required = ("name", "phase", "inputs", "preconditions", "authorization", "evidence", "side_effects", "output_state", "failure_class", "recovery", "idempotency", "references", "guidance")
    seen = set()
    for key, contract in contracts.items():
        if not isinstance(contract, ActionContract) or key != contract.name or key in seen:
            raise ActionContractError("action_contract_invalid", {"action": key})
        seen.add(key)
        value = contract.public()
        missing = [field for field in required if field != "side_effects" and not value.get(field)]
        if missing:
            raise ActionContractError("action_contract_incomplete", {"action": key, "missing": missing})
    return True


def contract_for(name):
    validate_contracts()
    try: return ACTION_CONTRACTS[name]
    except KeyError: raise ActionContractError("action_contract_unknown", {"action": name}) from None


def render_derived_checklist(contracts=ACTION_CONTRACTS):
    validate_contracts(contracts)
    lines = ["<!-- GENERATED FROM action_contracts.py; do not edit manually. -->", "| Action | Phase | Preconditions | Evidence | Recovery |", "|---|---|---|---|---|"]
    for name in sorted(contracts):
        item = contracts[name]
        lines.append(f"| `{name}` | `{item.phase}` | {', '.join(item.preconditions)} | {', '.join(item.evidence)} | {item.recovery} |")
    return "\n".join(lines) + "\n"


def render_cli_help(name=None):
    validate_contracts()
    selected = [contract_for(name)] if name is not None else [ACTION_CONTRACTS[key] for key in sorted(ACTION_CONTRACTS)]
    return {"generated": True, "source": "action_contracts.py", "actions": [item.public() for item in selected]}


def validate_action_surface(action_names):
    if not isinstance(action_names, list) or any(not isinstance(name, str) or not name.strip() for name in action_names):
        raise ActionContractError("action_surface_invalid")
    unknown = sorted(set(action_names) - set(ACTION_CONTRACTS))
    if unknown:
        raise ActionContractError("action_surface_unknown", {"actions": unknown})
    validate_contracts()
    return {"decision": "allow", "action_count": len(set(action_names)), "source": "action_contracts.py"}
