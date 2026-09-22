"""Legal lifecycle transitions for the SQLite controller."""
SPEC_TRANSITIONS={"planned":{"ready","blocked","cancelled"},"ready":{"ticketing","blocked"},"ticketing":{"tickets_ready","blocked"},"tickets_ready":{"implementing","blocked"},"implementing":{"handoff_received","blocked","cancelled"},"handoff_received":{"verifying","blocked"},"verifying":{"ready_to_merge","blocked"},"ready_to_merge":{"merged","blocked"},"merged":{"closed"},"blocked":{"ready","ticketing","implementing","cancelled"},"closed":set(),"cancelled":set()}
THREAD_TRANSITIONS={"created":{"route_verified","archived"},"route_verified":{"assigned","archived"},"assigned":{"working","archived"},"working":{"handoff_received","archived"},"handoff_received":{"verified"},"verified":{"archived"},"archived":set(),"tombstoned":set()}
def transition(table,current,target):
    if target not in table.get(current,set()): raise ValueError(f"illegal transition: {current} -> {target}")
