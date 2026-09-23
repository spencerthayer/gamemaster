; Two separate py-calls across the PeTTa bridge (receive set, later skill read).
!(py-call (sender_bridge_helpers.bootstrap))
!(py-call (sender_bridge_helpers.accept_sender))
!(bind! &observed (py-call (sender_bridge_helpers.read_sender)))
!(py-call (builtins.print &observed))
