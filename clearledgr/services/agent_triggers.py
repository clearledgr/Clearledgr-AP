"""
Agent Triggers Service for Clearledgr Reconciliation v1

Manages smart triggers for autonomous agent execution.
"""
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from clearledgr.services.agent_monitoring import (
    detect_data_changes,
    detect_period_end,
    should_trigger_reconciliation,
    check_data_threshold,
    get_suggested_period
)
from clearledgr.state.agent_memory import list_agent_schedules


class AgentTrigger:
    """Represents a trigger condition for agent execution."""
    
    def __init__(
        self,
        trigger_id: str,
        trigger_type: str,
        tool_type: str,
        tool_id: str,
        config: Dict,
        is_active: bool = True
    ):
        self.trigger_id = trigger_id
        self.trigger_type = trigger_type  # 'schedule', 'data_change', 'period_end', 'threshold'
        self.tool_type = tool_type  # 'sheets', 'excel', 'slack', 'teams'
        self.tool_id = tool_id
        self.config = config
        self.is_active = is_active
        self.last_triggered: Optional[str] = None
        self.trigger_count = 0
    
    def evaluate(self, context: Dict) -> bool:
        """
        Evaluate if this trigger should fire.
        
        Args:
            context: Context dict with current state (data, dates, etc.)
        
        Returns:
            True if trigger should fire
        """
        if not self.is_active:
            return False
        
        if self.trigger_type == "schedule":
            return should_trigger_reconciliation(
                self.last_triggered,
                self.config.get("schedule_type", "daily"),
                data_changed=context.get("data_changed", False),
                period_end_detected=context.get("period_end_detected", False),
                threshold_met=context.get("threshold_met", False)
            )
        
        elif self.trigger_type == "data_change":
            return context.get("data_changed", False)
        
        elif self.trigger_type == "period_end":
            period_info = detect_period_end()
            return period_info.get("is_month_end", False) or period_info.get("is_quarter_end", False)
        
        elif self.trigger_type == "threshold":
            current_count = context.get("data_count", 0)
            threshold = self.config.get("threshold", 100)
            threshold_met, _ = check_data_threshold(current_count, threshold)
            return threshold_met
        
        return False
    
    def get_suggested_config(self, context: Dict) -> Dict:
        """
        Get suggested configuration for this trigger.
        
        Args:
            context: Context dict with current state
        
        Returns:
            Suggested configuration dict
        """
        suggested = {}
        
        if self.trigger_type == "schedule":
            period_type = self.config.get("period_type", "monthly")
            suggested.update(get_suggested_period(period_type=period_type))
        
        # Add default tolerances if not in config
        if "amount_tolerance_pct" not in suggested:
            suggested["amount_tolerance_pct"] = self.config.get("amount_tolerance_pct", 0.5)
        
        if "date_window_days" not in suggested:
            suggested["date_window_days"] = self.config.get("date_window_days", 3)
        
        return suggested


class TriggerManager:
    """Manages agent triggers."""
    
    def __init__(self):
        self.triggers: Dict[str, AgentTrigger] = {}

    def load_triggers_from_store(self):
        """Load persisted schedules into in-memory triggers."""
        schedules = list_agent_schedules()
        for sched in schedules:
            if not sched.get("is_active"):
                continue
            schedule_type = sched.get("schedule_type")
            tool_type = sched.get("tool_type")
            tool_id = sched.get("tool_id")
            config = sched.get("schedule_config") or {}
            if schedule_type in ["daily", "weekly", "monthly", "on_change", "period_end"]:
                self.create_schedule_trigger(tool_type, tool_id, schedule_type, config)
            elif schedule_type == "threshold":
                threshold = config.get("threshold", 100)
                self.create_threshold_trigger(tool_type, tool_id, threshold, config)
    
    def register_trigger(self, trigger: AgentTrigger):
        """Register a trigger."""
        self.triggers[trigger.trigger_id] = trigger
    
    def get_triggers_for_tool(self, tool_type: str, tool_id: str) -> List[AgentTrigger]:
        """Get all active triggers for a specific tool."""
        return [
            t for t in self.triggers.values()
            if t.tool_type == tool_type and t.tool_id == tool_id and t.is_active
        ]
    
    def evaluate_triggers(
        self,
        tool_type: str,
        tool_id: str,
        context: Dict
    ) -> List[AgentTrigger]:
        """
        Evaluate all triggers for a tool and return those that should fire.
        
        Args:
            tool_type: Type of tool ('sheets', 'excel', etc.)
            tool_id: ID of the tool instance
            context: Context dict with current state
        
        Returns:
            List of triggers that should fire
        """
        triggers = self.get_triggers_for_tool(tool_type, tool_id)
        firing_triggers = []
        
        for trigger in triggers:
            if trigger.evaluate(context):
                firing_triggers.append(trigger)
                trigger.last_triggered = datetime.utcnow().isoformat()
                trigger.trigger_count += 1
        
        return firing_triggers
    
    def create_schedule_trigger(
        self,
        tool_type: str,
        tool_id: str,
        schedule_type: str,
        config: Dict
    ) -> AgentTrigger:
        """
        Create a schedule-based trigger.
        
        Args:
            tool_type: Type of tool
            tool_id: ID of tool instance
            schedule_type: 'daily', 'weekly', 'monthly', 'on_change', 'period_end'
            config: Additional configuration
        
        Returns:
            New AgentTrigger instance
        """
        trigger_id = f"{tool_type}_{tool_id}_schedule_{schedule_type}"
        
        trigger_config = {
            "schedule_type": schedule_type,
            "period_type": config.get("period_type", "monthly"),
            **config
        }
        
        trigger = AgentTrigger(
            trigger_id=trigger_id,
            trigger_type="schedule",
            tool_type=tool_type,
            tool_id=tool_id,
            config=trigger_config
        )
        
        self.register_trigger(trigger)
        return trigger
    
    def create_data_change_trigger(
        self,
        tool_type: str,
        tool_id: str,
        config: Dict
    ) -> AgentTrigger:
        """Create a data change trigger."""
        trigger_id = f"{tool_type}_{tool_id}_data_change"
        
        trigger = AgentTrigger(
            trigger_id=trigger_id,
            trigger_type="data_change",
            tool_type=tool_type,
            tool_id=tool_id,
            config=config
        )
        
        self.register_trigger(trigger)
        return trigger
    
    def create_threshold_trigger(
        self,
        tool_type: str,
        tool_id: str,
        threshold: int,
        config: Dict
    ) -> AgentTrigger:
        """Create a threshold-based trigger."""
        trigger_id = f"{tool_type}_{tool_id}_threshold_{threshold}"
        
        trigger_config = {
            "threshold": threshold,
            **config
        }
        
        trigger = AgentTrigger(
            trigger_id=trigger_id,
            trigger_type="threshold",
            tool_type=tool_type,
            tool_id=tool_id,
            config=trigger_config
        )
        
        self.register_trigger(trigger)
        return trigger


# Global trigger manager instance
trigger_manager = TriggerManager()
