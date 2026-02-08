"""
SAP Daily Sync Workflow for Clearledgr v1 (Autonomous Edition)

Implements the daily SAP data sync from product_spec_updated.md:
- DAILY 8:00am: Pull GL transactions from SAP
- Store in Sheets (SAP_Ledger_Export)
- Runs before 9:00am reconciliation

This is a Temporal workflow that can be scheduled to run daily.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, date
from typing import Dict, Any, List, Optional

from clearledgr.services.sap import SAPService


class SAPSyncService:
    """
    Service for syncing SAP GL data before reconciliation.
    
    Per product_spec_updated.md:
    - Runs daily at 8:00am (before reconciliation at 9:00am)
    - Pulls previous day's GL transactions
    - Updates SAP_Ledger_Export sheet automatically
    - Logs sync status in CLSUMMARY
    """
    
    def __init__(self, sap_service: Optional[SAPService] = None):
        self.sap = sap_service or SAPService()
    
    def pull_gl_transactions(
        self,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        company_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Pull GL transactions from SAP for the specified date range.
        
        Default: Previous day's transactions
        
        Args:
            start_date: Start of date range (default: yesterday)
            end_date: End of date range (default: yesterday)
            company_code: SAP company code (default: from env)
            
        Returns:
            {
                "status": "success" | "skipped" | "error",
                "transactions": [...],
                "count": int,
                "date_range": {"start": str, "end": str},
                "sync_timestamp": str,
                "error": Optional[str]
            }
        """
        # Default to yesterday's data
        today = date.today()
        if start_date is None:
            start_date = today - timedelta(days=1)
        if end_date is None:
            end_date = today - timedelta(days=1)
        
        try:
            transactions = self.sap.pull_gl_transactions(company_code)
            
            # Filter by date range if we got data
            if transactions:
                filtered = []
                for txn in transactions:
                    txn_date = txn.get("PostingDate") or txn.get("DocumentDate")
                    if txn_date:
                        try:
                            if isinstance(txn_date, str):
                                txn_date = datetime.fromisoformat(txn_date.split("T")[0]).date()
                            if start_date <= txn_date <= end_date:
                                filtered.append(txn)
                        except (ValueError, TypeError):
                            # Include if date parsing fails
                            filtered.append(txn)
                    else:
                        filtered.append(txn)
                transactions = filtered
            
            return {
                "status": "success" if transactions else "skipped",
                "transactions": transactions,
                "count": len(transactions),
                "date_range": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                },
                "sync_timestamp": datetime.utcnow().isoformat(),
                "reason": None if transactions else "No transactions or SAP not configured",
            }
            
        except Exception as e:
            return {
                "status": "error",
                "transactions": [],
                "count": 0,
                "date_range": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                },
                "sync_timestamp": datetime.utcnow().isoformat(),
                "error": str(e),
            }
    
    def format_for_sheets(self, transactions: List[Dict[str, Any]]) -> List[List[Any]]:
        """
        Format SAP transactions for SAP_Ledger_Export sheet.
        
        Returns header row + data rows matching spec format.
        """
        headers = [
            "document_number", "posting_date", "amount", "currency",
            "gl_account", "account_name", "cost_center", "reference", "description"
        ]
        
        rows = [headers]
        
        for txn in transactions:
            rows.append([
                txn.get("DocumentNumber", ""),
                txn.get("PostingDate", ""),
                txn.get("AmountInCompanyCodeCurrency", 0),
                txn.get("Currency", "EUR"),
                txn.get("GLAccount", ""),
                txn.get("GLAccountName", ""),
                txn.get("CostCenter", ""),
                txn.get("Reference", ""),
                txn.get("Text") or txn.get("Description", ""),
            ])
        
        return rows


# Temporal workflow definition (if Temporal is available)
try:
    from temporalio import workflow, activity
    from temporalio.common import RetryPolicy
    
    @activity.defn
    async def pull_sap_gl_activity(params: Dict[str, Any]) -> Dict[str, Any]:
        """Activity to pull GL data from SAP."""
        service = SAPSyncService()
        
        start_date = None
        end_date = None
        
        if params.get("start_date"):
            start_date = date.fromisoformat(params["start_date"])
        if params.get("end_date"):
            end_date = date.fromisoformat(params["end_date"])
        
        return service.pull_gl_transactions(
            start_date=start_date,
            end_date=end_date,
            company_code=params.get("company_code"),
        )
    
    @activity.defn
    async def update_sheets_activity(params: Dict[str, Any]) -> Dict[str, Any]:
        """Activity to update SAP_Ledger_Export sheet."""
        from clearledgr.services.sheets_integration import SheetsService
        
        transactions = params.get("transactions", [])
        count = params.get("count", 0)
        spreadsheet_id = params.get("spreadsheet_id") or os.getenv("CLEARLEDGR_SPREADSHEET_ID")
        
        if not spreadsheet_id:
            return {
                "status": "skipped",
                "reason": "No spreadsheet ID configured",
                "row_count": 0,
            }
        
        if not transactions:
            return {
                "status": "skipped",
                "reason": "No transactions to write",
                "row_count": 0,
            }
        
        try:
            # Format transactions for sheets
            service = SAPSyncService()
            rows = service.format_for_sheets(transactions)
            
            # Write to SAP_Ledger_Export sheet
            sheets = SheetsService(spreadsheet_id)
            result = sheets.write_range(
                range_name="SAP_Ledger_Export!A1",
                values=rows,
                clear_first=True,
            )
            
            return {
                "status": "success",
                "sheet_updated": "SAP_Ledger_Export",
                "row_count": count,
                "cells_updated": result.get("updatedCells", 0),
            }
        except Exception as e:
            return {
                "status": "error",
                "error": str(e),
                "row_count": 0,
            }
    
    @activity.defn
    async def notify_sync_complete_activity(params: Dict[str, Any]) -> Dict[str, Any]:
        """Activity to send Slack notification on sync completion."""
        import httpx
        
        webhook_url = os.getenv("SLACK_WEBHOOK_URL")
        
        if not webhook_url:
            return {"status": "skipped", "reason": "No Slack webhook configured"}
        
        try:
            if params.get("status") == "success":
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*SAP GL Sync Complete*\n"
                                   f"Transactions: {params.get('count', 0)}\n"
                                   f"Date range: {params.get('date_range', {}).get('start')} to "
                                   f"{params.get('date_range', {}).get('end')}\n"
                                   f"Ready for reconciliation at 09:00"
                        }
                    }
                ]
            else:
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*SAP GL Sync Issue*\n"
                                   f"Status: {params.get('status')}\n"
                                   f"Reason: {params.get('error') or params.get('reason') or 'Unknown'}\n"
                                   f"Reconciliation may use cached data"
                        }
                    }
                ]
            
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    webhook_url,
                    json={"blocks": blocks},
                    timeout=10,
                )
                
                if response.status_code == 200:
                    return {"status": "notified"}
                else:
                    return {"status": "notification_failed", "error": f"HTTP {response.status_code}"}
            
        except Exception as e:
            return {"status": "notification_failed", "error": str(e)}
    
    @workflow.defn
    class DailySAPSyncWorkflow:
        """
        Daily SAP GL sync workflow.
        
        Scheduled to run at 8:00am daily, before reconciliation at 9:00am.
        
        Steps:
        1. Pull GL transactions from SAP
        2. Update SAP_Ledger_Export sheet
        3. Log sync status
        4. Notify on completion (optional)
        """
        
        @workflow.run
        async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
            """
            Execute daily SAP sync.
            
            Args:
                params: {
                    "company_code": Optional[str],
                    "start_date": Optional[str],  # ISO format
                    "end_date": Optional[str],
                    "notify": bool,  # Whether to send Slack notification
                }
                
            Returns:
                Sync result with status and transaction count
            """
            workflow.logger.info("Starting daily SAP GL sync")
            
            # Step 1: Pull GL transactions
            sync_result = await workflow.execute_activity(
                pull_sap_gl_activity,
                args=[params],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )
            
            if sync_result.get("status") == "success" and sync_result.get("transactions"):
                # Step 2: Update Sheets
                sheets_result = await workflow.execute_activity(
                    update_sheets_activity,
                    args=[{
                        "transactions": sync_result["transactions"],
                        "count": sync_result["count"],
                    }],
                    start_to_close_timeout=timedelta(minutes=2),
                )
                sync_result["sheets_updated"] = sheets_result.get("status") == "success"
            
            # Step 3: Notify if requested
            if params.get("notify", True):
                await workflow.execute_activity(
                    notify_sync_complete_activity,
                    args=[sync_result],
                    start_to_close_timeout=timedelta(seconds=30),
                )
            
            workflow.logger.info(f"SAP sync complete: {sync_result.get('count', 0)} transactions")
            
            return sync_result

except ImportError:
    # Temporal not available, provide sync function for direct use
    pass


def run_sap_sync(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    company_code: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run SAP sync directly (without Temporal).
    
    Use this for testing or when Temporal is not available.
    """
    service = SAPSyncService()
    return service.pull_gl_transactions(
        start_date=start_date,
        end_date=end_date,
        company_code=company_code,
    )
