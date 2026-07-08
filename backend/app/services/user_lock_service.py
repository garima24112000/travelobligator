from __future__ import annotations

from datetime import datetime, timezone

from app.models.planning_state import PlanningState, UserLock


class UserLockService:
    """Owns `user_locks` (docs/14_backend_architecture.md section 16).

    Locked items should not be changed by regeneration unless the user
    explicitly asks, the lock conflicts with a higher-priority constraint,
    the plan becomes infeasible, or provider data shows the item is
    unavailable (docs/09_planning_state.md section 12).
    """

    def add_lock(
        self,
        planning_state: PlanningState,
        locked_item_type: str,
        locked_item_id: str,
        reason: str = "user_approved",
    ) -> PlanningState:
        planning_state.user_locks.append(
            UserLock(
                locked_item_type=locked_item_type,
                locked_item_id=locked_item_id,
                reason=reason,
            )
        )
        planning_state.touch()
        return planning_state

    def remove_lock(self, planning_state: PlanningState, lock_id: str) -> PlanningState:
        for lock in planning_state.user_locks:
            if lock.lock_id == lock_id and lock.is_active:
                lock.is_active = False
                lock.removed_at = datetime.now(timezone.utc)
                break
        planning_state.touch()
        return planning_state

    def is_locked(self, planning_state: PlanningState, locked_item_id: str) -> bool:
        return any(
            lock.locked_item_id == locked_item_id and lock.is_active
            for lock in planning_state.user_locks
        )


user_lock_service = UserLockService()
