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
        reason: str = "user_requested_keep",
    ) -> PlanningState:
        existing_active_lock = self.find_active_lock(
            planning_state, locked_item_type, locked_item_id
        )
        if existing_active_lock is not None:
            return planning_state

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

    def find_lock(self, planning_state: PlanningState, lock_id: str) -> UserLock | None:
        for lock in planning_state.user_locks:
            if lock.lock_id == lock_id:
                return lock
        return None

    def find_active_lock(
        self, planning_state: PlanningState, locked_item_type: str, locked_item_id: str
    ) -> UserLock | None:
        for lock in planning_state.user_locks:
            if (
                lock.is_active
                and lock.locked_item_type == locked_item_type
                and lock.locked_item_id == locked_item_id
            ):
                return lock
        return None

    def is_locked(self, planning_state: PlanningState, locked_item_id: str) -> bool:
        return any(
            lock.locked_item_id == locked_item_id and lock.is_active
            for lock in planning_state.user_locks
        )


user_lock_service = UserLockService()
