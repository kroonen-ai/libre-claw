"""Heartbeat system for Libre Claw.

Provides async heartbeat loop with cadence checks for autonomous task execution.
"""

import asyncio
from typing import Any, Callable, Dict, List, Optional

from .config import HeartbeatConfig
from .workspace import Workspace


class Heartbeat:
    """Heartbeat system for autonomous task execution.

    Runs periodic checks during idle periods, executing tasks from HEARTBEAT.md.
    """

    def __init__(
        self,
        workspace: Workspace,
        config: Optional[HeartbeatConfig] = None,
        on_tick: Optional[Callable[[], Any]] = None,
    ):
        """Initialize heartbeat system.

        Args:
            workspace: Workspace instance
            config: Heartbeat configuration
            on_tick: Optional callback for each tick
        """
        self.workspace = workspace
        self.config = config or HeartbeatConfig()
        self.on_tick = on_tick

        self._running = False
        self._task: Optional[asyncio.Task] = None

    @property
    def is_running(self) -> bool:
        """Check if heartbeat is running."""
        return self._running

    async def start(self) -> None:
        """Start the heartbeat loop."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run_loop(self) -> None:
        """Main heartbeat loop."""
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                print(f"Heartbeat tick error: {e}")
                self.workspace.update_heartbeat_audit("FAILED", str(e))

            # Wait for next interval
            await asyncio.sleep(self.config.interval_seconds)

    async def _tick(self) -> None:
        """Execute a single heartbeat tick."""
        # Get heartbeat state
        state = self.workspace.get_heartbeat_state()

        # Check if we should run
        if self._should_skip(state):
            return

        # Execute callback if provided
        if self.on_tick:
            result = self.on_tick()
            if asyncio.iscoroutine(result):
                await result

        # Update audit log
        self.workspace.update_heartbeat_audit("SUCCESS", f"Session #{state.get('total_runs', 0) + 1}")

    def _should_skip(self, state: Dict[str, Any]) -> bool:
        """Determine if heartbeat should skip this tick.

        Args:
            state: Current heartbeat state

        Returns:
            True if should skip
        """
        # Skip if disabled
        if not self.config.enabled:
            return True

        # Skip if too many consecutive failures (circuit breaker)
        if state.get("consecutive_failures", 0) >= 5:
            print("Heartbeat circuit breaker: too many consecutive failures")
            return True

        return False

    async def trigger(self) -> bool:
        """Manually trigger a heartbeat tick.

        Returns:
            True if tick was executed
        """
        if not self._running:
            return False

        try:
            await self._tick()
            return True
        except Exception as e:
            print(f"Manual heartbeat trigger failed: {e}")
            return False


class HeartbeatManager:
    """Manages multiple heartbeat instances."""

    def __init__(self):
        """Initialize heartbeat manager."""
        self._heartbeats: Dict[str, Heartbeat] = {}

    def add(
        self,
        name: str,
        workspace: Workspace,
        config: Optional[HeartbeatConfig] = None,
        on_tick: Optional[Callable[[], Any]] = None,
    ) -> Heartbeat:
        """Add a heartbeat instance.

        Args:
            name: Name of the heartbeat
            workspace: Workspace instance
            config: Heartbeat configuration
            on_tick: Optional tick callback

        Returns:
            Heartbeat instance
        """
        heartbeat = Heartbeat(workspace, config, on_tick)
        self._heartbeats[name] = heartbeat
        return heartbeat

    def get(self, name: str) -> Optional[Heartbeat]:
        """Get a heartbeat by name.

        Args:
            name: Heartbeat name

        Returns:
            Heartbeat instance or None
        """
        return self._heartbeats.get(name)

    async def start_all(self) -> None:
        """Start all heartbeats."""
        for heartbeat in self._heartbeats.values():
            await heartbeat.start()

    async def stop_all(self) -> None:
        """Stop all heartbeats."""
        for heartbeat in self._heartbeats.values():
            await heartbeat.stop()

    def list_heartbeats(self) -> List[str]:
        """List all heartbeat names.

        Returns:
            List of heartbeat names
        """
        return list(self._heartbeats.keys())
