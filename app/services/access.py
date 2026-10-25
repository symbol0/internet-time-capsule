def is_user_allowed(user_id: int, allowed_user_ids: set[int]) -> bool:
    if not allowed_user_ids:
        return True
    return user_id in allowed_user_ids


def can_manage(actor_id: int, owner_id: int) -> bool:
    return actor_id == owner_id
