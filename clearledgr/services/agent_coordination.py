"""
Agent Coordination Service for Clearledgr Reconciliation v1

Enables multi-agent coordination and shared state across tools.
"""
from typing import Dict, List, Optional
from datetime import datetime, timezone
from clearledgr.state.agent_memory import (
    record_agent_event, get_recent_agent_events,
    get_agent_memory, save_agent_memory
)
from clearledgr.services.shared_state import get_shared_state, set_shared_state, init_shared_state_db


class AgentCoordinator:
    """Coordinates agents across different tools."""
    
    def __init__(self):
        init_shared_state_db()
    
    def broadcast_event(
        self,
        event_type: str,
        source_agent: str,
        source_id: str,
        organization_id: str,
        payload: Dict
    ) -> str:
        """
        Broadcast an event to all agents.
        
        Args:
            event_type: Type of event
            source_agent: Source agent type
            source_id: Source agent ID
            organization_id: Organization ID
            payload: Event payload
        
        Returns:
            Event ID
        """
        event_id = f"{source_agent}_{source_id}_{datetime.now(timezone.utc).timestamp()}"
        
        record_agent_event(
            event_id=event_id,
            event_type=event_type,
            source_agent=source_agent,
            source_id=source_id,
            organization_id=organization_id,
            payload=payload
        )
        
        return event_id
    
    def get_shared_state(
        self,
        organization_id: str,
        key: Optional[str] = None
    ) -> Dict:
        """
        Get shared state for an organization.
        
        Args:
            organization_id: Organization ID
            key: Optional key to get specific state
        
        Returns:
            Shared state dict
        """
        return get_shared_state(organization_id, key)
    
    def update_shared_state(
        self,
        organization_id: str,
        key: str,
        value: Dict
    ):
        """
        Update shared state.
        
        Args:
            organization_id: Organization ID
            key: State key
            value: State value
        """
        set_shared_state(
            organization_id,
            key,
            {**value, "updated_at": datetime.now(timezone.utc).isoformat()},
        )
    
    def sync_config_across_agents(
        self,
        organization_id: str,
        config: Dict,
        source_agent: str
    ):
        """
        Sync configuration across all agents for an organization.
        
        Args:
            organization_id: Organization ID
            config: Configuration to sync
            source_agent: Source agent that created/updated config
        """
        # Save to shared memory
        memory_id = f"{organization_id}_shared_config"
        
        save_agent_memory(
            memory_id=memory_id,
            organization_id=organization_id,
            tool_type="shared",
            memory_type="config",
            key="reconciliation_config",
            value=config,
            confidence=1.0
        )
        
        # Broadcast event
        self.broadcast_event(
            event_type="config_updated",
            source_agent=source_agent,
            source_id=organization_id,
            organization_id=organization_id,
            payload={
                "config": config,
                "source": source_agent
            }
        )
    
    def get_synced_config(
        self,
        organization_id: str
    ) -> Optional[Dict]:
        """
        Get synced configuration for an organization.
        
        Args:
            organization_id: Organization ID
        
        Returns:
            Synced configuration or None
        """
        memory = get_agent_memory(
            organization_id=organization_id,
            tool_type="shared",
            memory_type="config",
            key="reconciliation_config"
        )
        
        if memory:
            return memory[0]["value"]
        
        return None
    
    def notify_agents(
        self,
        organization_id: str,
        notification_type: str,
        message: str,
        source_agent: str,
        source_id: str
    ):
        """
        Notify all agents about an event.
        
        Args:
            organization_id: Organization ID
            notification_type: Type of notification
            message: Notification message
            source_agent: Source agent
            source_id: Source agent ID
        """
        self.broadcast_event(
            event_type=f"notification_{notification_type}",
            source_agent=source_agent,
            source_id=source_id,
            organization_id=organization_id,
            payload={
                "type": notification_type,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )
    
    def get_agent_activity(
        self,
        organization_id: str,
        limit: int = 20
    ) -> List[Dict]:
        """
        Get recent agent activity across all tools.
        
        Args:
            organization_id: Organization ID
            limit: Maximum number of events to return
        
        Returns:
            List of recent events
        """
        return get_recent_agent_events(
            organization_id=organization_id,
            limit=limit
        )
    
    def coordinate_reconciliation(
        self,
        organization_id: str,
        tool_type: str,
        tool_id: str,
        run_id: str,
        result: Dict
    ):
        """
        Coordinate reconciliation across agents.
        
        Args:
            organization_id: Organization ID
            tool_type: Tool type that ran reconciliation
            tool_id: Tool ID
            run_id: Run ID
            result: Reconciliation result
        """
        # Broadcast reconciliation event
        self.broadcast_event(
            event_type="reconciliation_completed",
            source_agent=tool_type,
            source_id=tool_id,
            organization_id=organization_id,
            payload={
                "run_id": run_id,
                "summary": result.get("summary", []),
                "reconciled_count": len(result.get("reconciled", [])),
                "exceptions_count": len(result.get("exceptions", []))
            }
        )
        
        # Update shared state
        self.update_shared_state(
            organization_id=organization_id,
            key="last_reconciliation",
            value={
                "run_id": run_id,
                "tool_type": tool_type,
                "tool_id": tool_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "summary": result.get("summary", [])
            }
        )


# Global coordinator instance
agent_coordinator = AgentCoordinator()
