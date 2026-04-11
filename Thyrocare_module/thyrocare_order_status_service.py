from Orders_module.Order_model import OrderStatus

def _map_thyrocare_status(thyro_status: str) -> OrderStatus:
    """
    Maps Thyrocare specific order status to our internal OrderStatus enum.
    """
    status_map = {
        "YET TO ASSIGN": OrderStatus.CONFIRMED,
        "ASSIGNED": OrderStatus.SCHEDULED,
        "ACCEPTED": OrderStatus.SCHEDULED,
        "STARTED": OrderStatus.SCHEDULE_CONFIRMED_BY_LAB,
        "ARRIVED": OrderStatus.SAMPLE_COLLECTED,
        "CONFIRMED": OrderStatus.SAMPLE_COLLECTED,
        "DONE": OrderStatus.REPORT_READY,
        "REPORTED": OrderStatus.REPORT_READY,
        "CANCELLED": OrderStatus.PAYMENT_FAILED,  # closest terminal failure state available
    }
    
    return status_map.get(thyro_status.upper(), OrderStatus.CONFIRMED)

def _is_terminal_state(thyro_status: str) -> bool:
    """
    Determines if a status represents a completed or cancelled order.
    """
    terminal_statuses = ["DONE", "REPORTED", "CANCELLED"]
    return thyro_status.upper() in terminal_statuses
