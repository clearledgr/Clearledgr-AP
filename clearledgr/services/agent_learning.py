"""
Agent Learning Service for Clearledgr v1

Enables agents to learn from user feedback and adapt behavior.
"""
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from clearledgr.state.agent_memory import (
    save_agent_memory, get_agent_memory, save_agent_feedback
)


def get_reconciliation_run(run_id: str) -> Optional[Dict]:
    """Get reconciliation run by ID from run history."""
    from clearledgr.state.run_history import get_run
    import json
    
    run = get_run(run_id)
    if run and run.get("config_json"):
        try:
            run["config"] = json.loads(run["config_json"])
        except:
            run["config"] = {}
    return run


def learn_from_feedback(
    feedback_id: str,
    run_id: str,
    feedback_type: str,
    original_result: Dict,
    corrected_result: Optional[Dict] = None,
    user_notes: Optional[str] = None,
    organization_id: Optional[str] = None
) -> Dict:
    """
    Process user feedback and update agent memory.
    
    Args:
        feedback_id: Unique feedback ID
        run_id: Run ID this feedback relates to
        feedback_type: 'correction', 'approval', 'rejection', 'suggestion'
        original_result: Original reconciliation result
        corrected_result: Corrected result (if correction)
        user_notes: User notes
        organization_id: Organization ID
    
    Returns:
        Dict with learning outcomes
    """
    # Save feedback
    save_agent_feedback(
        feedback_id=feedback_id,
        run_id=run_id,
        feedback_type=feedback_type,
        original_result=original_result,
        corrected_result=corrected_result,
        user_notes=user_notes
    )
    
    learning_outcomes = {
        "feedback_saved": True,
        "adaptations": []
    }
    
    # Get run details to extract config
    run = get_reconciliation_run(run_id)
    if not run:
        return learning_outcomes
    
    config = run.get("config", {})
    tool_type = run.get("source_type", "csv")
    
    # Learn from corrections
    if feedback_type == "correction" and corrected_result:
        adaptations = _learn_from_correction(
            organization_id=organization_id or "default",
            tool_type=tool_type,
            original_result=original_result,
            corrected_result=corrected_result,
            config=config
        )
        learning_outcomes["adaptations"].extend(adaptations)
    
    # Learn from approvals (reinforce good matches)
    elif feedback_type == "approval":
        adaptations = _learn_from_approval(
            organization_id=organization_id or "default",
            tool_type=tool_type,
            original_result=original_result,
            config=config
        )
        learning_outcomes["adaptations"].extend(adaptations)
    
    # Learn from rejections (avoid bad matches)
    elif feedback_type == "rejection":
        adaptations = _learn_from_rejection(
            organization_id=organization_id or "default",
            tool_type=tool_type,
            original_result=original_result,
            config=config
        )
        learning_outcomes["adaptations"].extend(adaptations)
    
    return learning_outcomes


def _learn_from_correction(
    organization_id: str,
    tool_type: str,
    original_result: Dict,
    corrected_result: Dict,
    config: Dict
) -> List[Dict]:
    """Learn from user corrections."""
    adaptations = []
    
    # Extract tolerance from config
    original_tolerance = config.get("amount_tolerance_pct", 0.5)
    
    # Analyze what changed
    # If user corrected a match, we might need to adjust tolerance
    if "tolerance_adjustment" in corrected_result:
        new_tolerance = corrected_result["tolerance_adjustment"]
        memory_id = f"{organization_id}_{tool_type}_tolerance"
        
        # Calculate confidence based on how different it is
        diff = abs(new_tolerance - original_tolerance)
        confidence = min(0.9, 0.5 + (diff * 0.1))  # Higher confidence for larger changes
        
        save_agent_memory(
            memory_id=memory_id,
            organization_id=organization_id,
            tool_type=tool_type,
            memory_type="tolerance",
            key="amount_tolerance_pct",
            value=new_tolerance,
            confidence=confidence
        )
        
        adaptations.append({
            "type": "tolerance_adjustment",
            "old_value": original_tolerance,
            "new_value": new_tolerance,
            "confidence": confidence
        })
    
    # Learn date window adjustments
    if "date_window_adjustment" in corrected_result:
        original_window = config.get("date_window_days", 3)
        new_window = corrected_result["date_window_adjustment"]
        memory_id = f"{organization_id}_{tool_type}_date_window"
        
        diff = abs(new_window - original_window)
        confidence = min(0.9, 0.5 + (diff * 0.05))
        
        save_agent_memory(
            memory_id=memory_id,
            organization_id=organization_id,
            tool_type=tool_type,
            memory_type="date_window",
            key="date_window_days",
            value=new_window,
            confidence=confidence
        )
        
        adaptations.append({
            "type": "date_window_adjustment",
            "old_value": original_window,
            "new_value": new_window,
            "confidence": confidence
        })
    
    return adaptations


def _learn_from_approval(
    organization_id: str,
    tool_type: str,
    original_result: Dict,
    config: Dict
) -> List[Dict]:
    """Learn from user approvals (reinforce good configurations)."""
    adaptations = []
    
    # Reinforce current tolerance
    tolerance = config.get("amount_tolerance_pct", 0.5)
    memory_id = f"{organization_id}_{tool_type}_tolerance"
    
    # Get existing memory
    existing = get_agent_memory(
        organization_id=organization_id,
        tool_type=tool_type,
        memory_type="tolerance",
        key="amount_tolerance_pct"
    )
    
    if existing:
        # Increase confidence slightly
        current_confidence = existing[0].get("confidence", 0.5)
        new_confidence = min(1.0, current_confidence + 0.05)
        
        save_agent_memory(
            memory_id=memory_id,
            organization_id=organization_id,
            tool_type=tool_type,
            memory_type="tolerance",
            key="amount_tolerance_pct",
            value=tolerance,
            confidence=new_confidence
        )
        
        adaptations.append({
            "type": "confidence_increase",
            "value": tolerance,
            "confidence": new_confidence
        })
    else:
        # Create new memory with moderate confidence
        save_agent_memory(
            memory_id=memory_id,
            organization_id=organization_id,
            tool_type=tool_type,
            memory_type="tolerance",
            key="amount_tolerance_pct",
            value=tolerance,
            confidence=0.6
        )
    
    return adaptations


def _learn_from_rejection(
    organization_id: str,
    tool_type: str,
    original_result: Dict,
    config: Dict
) -> List[Dict]:
    """Learn from user rejections (avoid bad configurations)."""
    adaptations = []
    
    # Decrease confidence in current tolerance
    tolerance = config.get("amount_tolerance_pct", 0.5)
    memory_id = f"{organization_id}_{tool_type}_tolerance"
    
    existing = get_agent_memory(
        organization_id=organization_id,
        tool_type=tool_type,
        memory_type="tolerance",
        key="amount_tolerance_pct"
    )
    
    if existing:
        # Decrease confidence
        current_confidence = existing[0].get("confidence", 0.5)
        new_confidence = max(0.1, current_confidence - 0.1)
        
        save_agent_memory(
            memory_id=memory_id,
            organization_id=organization_id,
            tool_type=tool_type,
            memory_type="tolerance",
            key="amount_tolerance_pct",
            value=tolerance,
            confidence=new_confidence
        )
        
        adaptations.append({
            "type": "confidence_decrease",
            "value": tolerance,
            "confidence": new_confidence
        })
    
    return adaptations


def get_learned_config(
    organization_id: str,
    tool_type: str,
    default_config: Dict
) -> Dict:
    """
    Get configuration with learned parameters applied.
    
    Args:
        organization_id: Organization ID
        tool_type: Tool type
        default_config: Default configuration
    
    Returns:
        Configuration with learned parameters
    """
    learned_config = default_config.copy()
    
    # Get learned tolerance
    tolerance_memory = get_agent_memory(
        organization_id=organization_id,
        tool_type=tool_type,
        memory_type="tolerance",
        key="amount_tolerance_pct"
    )
    
    if tolerance_memory:
        memory = tolerance_memory[0]
        if memory["confidence"] > 0.6:  # Only use if confident
            learned_config["amount_tolerance_pct"] = memory["value"]
    
    # Get learned date window
    date_window_memory = get_agent_memory(
        organization_id=organization_id,
        tool_type=tool_type,
        memory_type="date_window",
        key="date_window_days"
    )
    
    if date_window_memory:
        memory = date_window_memory[0]
        if memory["confidence"] > 0.6:
            learned_config["date_window_days"] = memory["value"]
    
    # Get learned column mappings
    column_mappings = get_agent_memory(
        organization_id=organization_id,
        tool_type=tool_type,
        memory_type="column_mapping"
    )
    
    if column_mappings:
        # Use most confident mappings
        for mapping in column_mappings:
            if mapping["confidence"] > 0.7:
                if "mappings" not in learned_config:
                    learned_config["mappings"] = {}
                if tool_type not in learned_config["mappings"]:
                    learned_config["mappings"][tool_type] = {}
                learned_config["mappings"][tool_type][mapping["key"]] = mapping["value"]
    
    return learned_config


def calculate_adaptation_confidence(
    success_count: int,
    total_count: int,
    base_confidence: float = 0.5
) -> float:
    """
    Calculate confidence in an adaptation based on success rate.
    
    Args:
        success_count: Number of successful uses
        total_count: Total number of uses
        base_confidence: Base confidence level
    
    Returns:
        Confidence score (0.0 to 1.0)
    """
    if total_count == 0:
        return base_confidence
    
    success_rate = success_count / total_count
    
    # Confidence increases with success rate and usage count
    usage_factor = min(1.0, total_count / 10.0)  # Full confidence after 10 uses
    confidence = base_confidence + (success_rate * 0.4 * usage_factor)
    
    return min(1.0, max(0.1, confidence))

