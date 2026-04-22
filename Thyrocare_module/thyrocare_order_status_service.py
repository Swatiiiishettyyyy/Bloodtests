from Orders_module.Order_model import OrderStatus


def _map_thyrocare_status(thyro_status: str) -> OrderStatus:
    """
    Maps Thyrocare orderStatus to our internal OrderStatus enum.

    IMPORTANT: Blood test order status is owned by ThyrocareOrderTracking, NOT by
    order_items.order_status. This function is only used for the CANCELLED propagation
    path in _sync_nucleotide_order_from_thyrocare_webhook (which is otherwise a no-op).

    Frontend display for blood tests uses _THYROCARE_STATUS_INFO in Thyrocare_router.py.
    Frontend display for genetic tests uses order_items.order_status directly.
    """
    status_map = {
        "CANCELLED": OrderStatus.CANCELLED,
    }
    return status_map.get(thyro_status.upper().strip(), OrderStatus.CONFIRMED)


def _is_terminal_state(thyro_status: str) -> bool:
    terminal_statuses = ["DONE", "REPORTED", "CANCELLED"]
    return thyro_status.upper() in terminal_statuses
