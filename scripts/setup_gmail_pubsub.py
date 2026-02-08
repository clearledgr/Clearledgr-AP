#!/usr/bin/env python3
"""
Gmail Pub/Sub Setup Script

This script sets up Google Cloud Pub/Sub for 24/7 Gmail push notifications.
Run this once during initial deployment.

Prerequisites:
1. Google Cloud project with Gmail API enabled
2. Pub/Sub API enabled
3. Service account with Pub/Sub Admin role
4. OAuth 2.0 credentials for web application

Usage:
    python scripts/setup_gmail_pubsub.py --project-id YOUR_PROJECT_ID

Environment Variables Required:
    GOOGLE_CLOUD_PROJECT - Your GCP project ID
    GOOGLE_APPLICATION_CREDENTIALS - Path to service account key JSON
    CLEARLEDGR_WEBHOOK_URL - Your production webhook URL (e.g., https://api.clearledgr.com/gmail/push)
"""

import argparse
import json
import os
import sys
from typing import Optional

try:
    from google.cloud import pubsub_v1
    from google.api_core.exceptions import AlreadyExists, NotFound
except ImportError:
    print("Error: google-cloud-pubsub not installed")
    print("Run: pip install google-cloud-pubsub")
    sys.exit(1)


# Configuration
TOPIC_NAME = "clearledgr-gmail-push"
SUBSCRIPTION_NAME = "clearledgr-gmail-push-sub"
GMAIL_PUBLISHER = "serviceAccount:gmail-api-push@system.gserviceaccount.com"


def setup_pubsub(
    project_id: str,
    webhook_url: str,
    topic_name: str = TOPIC_NAME,
    subscription_name: str = SUBSCRIPTION_NAME,
) -> dict:
    """
    Set up Pub/Sub topic and subscription for Gmail push notifications.
    
    Args:
        project_id: Google Cloud project ID
        webhook_url: HTTPS endpoint to receive push notifications
        topic_name: Name for the Pub/Sub topic
        subscription_name: Name for the push subscription
        
    Returns:
        dict with topic and subscription details
    """
    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    
    topic_path = publisher.topic_path(project_id, topic_name)
    subscription_path = subscriber.subscription_path(project_id, subscription_name)
    
    result = {
        "project_id": project_id,
        "topic_path": topic_path,
        "subscription_path": subscription_path,
        "webhook_url": webhook_url,
    }
    
    # Step 1: Create topic
    print(f"\n1. Creating Pub/Sub topic: {topic_path}")
    try:
        topic = publisher.create_topic(request={"name": topic_path})
        print(f"   ✅ Created topic: {topic.name}")
        result["topic_created"] = True
    except AlreadyExists:
        print(f"   ℹ️  Topic already exists")
        result["topic_created"] = False
    
    # Step 2: Grant Gmail permission to publish to topic
    print(f"\n2. Granting Gmail API publish permission...")
    try:
        policy = publisher.get_iam_policy(request={"resource": topic_path})
        
        # Add Gmail as publisher
        gmail_binding = {
            "role": "roles/pubsub.publisher",
            "members": [GMAIL_PUBLISHER],
        }
        
        # Check if binding already exists
        binding_exists = any(
            b.role == gmail_binding["role"] and GMAIL_PUBLISHER in b.members
            for b in policy.bindings
        )
        
        if not binding_exists:
            policy.bindings.append(gmail_binding)
            publisher.set_iam_policy(
                request={"resource": topic_path, "policy": policy}
            )
            print(f"   ✅ Granted publish permission to Gmail API")
        else:
            print(f"   ℹ️  Gmail API already has publish permission")
        
        result["gmail_permission"] = True
    except Exception as e:
        print(f"   ❌ Error setting IAM policy: {e}")
        result["gmail_permission"] = False
    
    # Step 3: Create push subscription
    print(f"\n3. Creating push subscription: {subscription_path}")
    print(f"   Webhook URL: {webhook_url}")
    
    try:
        # Delete existing subscription if it exists (to update webhook URL)
        try:
            subscriber.delete_subscription(request={"subscription": subscription_path})
            print(f"   ℹ️  Deleted existing subscription")
        except NotFound:
            pass
        
        subscription = subscriber.create_subscription(
            request={
                "name": subscription_path,
                "topic": topic_path,
                "push_config": {
                    "push_endpoint": webhook_url,
                    "attributes": {
                        "x-goog-version": "v1",
                    },
                },
                "ack_deadline_seconds": 60,  # 60 seconds to process
                "message_retention_duration": {"seconds": 604800},  # 7 days
                "retry_policy": {
                    "minimum_backoff": {"seconds": 10},
                    "maximum_backoff": {"seconds": 600},
                },
            }
        )
        print(f"   ✅ Created subscription: {subscription.name}")
        result["subscription_created"] = True
    except AlreadyExists:
        print(f"   ℹ️  Subscription already exists")
        result["subscription_created"] = False
    except Exception as e:
        print(f"   ❌ Error creating subscription: {e}")
        result["subscription_created"] = False
    
    return result


def verify_setup(project_id: str, topic_name: str = TOPIC_NAME) -> bool:
    """Verify the Pub/Sub setup is correct."""
    publisher = pubsub_v1.PublisherClient()
    subscriber = pubsub_v1.SubscriberClient()
    
    topic_path = publisher.topic_path(project_id, topic_name)
    subscription_path = subscriber.subscription_path(project_id, f"{topic_name}-sub")
    
    print("\n4. Verifying setup...")
    
    # Check topic
    try:
        topic = publisher.get_topic(request={"topic": topic_path})
        print(f"   ✅ Topic exists: {topic.name}")
    except NotFound:
        print(f"   ❌ Topic not found")
        return False
    
    # Check subscription
    try:
        subscription = subscriber.get_subscription(request={"subscription": subscription_path})
        print(f"   ✅ Subscription exists: {subscription.name}")
        print(f"   ✅ Push endpoint: {subscription.push_config.push_endpoint}")
    except NotFound:
        print(f"   ❌ Subscription not found")
        return False
    
    # Check IAM
    policy = publisher.get_iam_policy(request={"resource": topic_path})
    has_gmail = any(
        GMAIL_PUBLISHER in b.members 
        for b in policy.bindings 
        if b.role == "roles/pubsub.publisher"
    )
    
    if has_gmail:
        print(f"   ✅ Gmail API has publish permission")
    else:
        print(f"   ❌ Gmail API missing publish permission")
        return False
    
    return True


def generate_env_config(project_id: str, topic_name: str = TOPIC_NAME) -> str:
    """Generate environment configuration for the backend."""
    return f"""
# Gmail Pub/Sub Configuration
# Add these to your .env file

GOOGLE_CLOUD_PROJECT={project_id}
GMAIL_PUBSUB_TOPIC=projects/{project_id}/topics/{topic_name}
GMAIL_PUBSUB_SUBSCRIPTION=projects/{project_id}/subscriptions/{topic_name}-sub

# OAuth Configuration (get from Google Cloud Console)
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=https://api.clearledgr.com/gmail/callback

# For local development
# GOOGLE_REDIRECT_URI=http://localhost:8000/gmail/callback
"""


def main():
    parser = argparse.ArgumentParser(
        description="Set up Gmail Pub/Sub for Clearledgr"
    )
    parser.add_argument(
        "--project-id",
        default=os.environ.get("GOOGLE_CLOUD_PROJECT"),
        help="Google Cloud project ID",
    )
    parser.add_argument(
        "--webhook-url",
        default=os.environ.get("CLEARLEDGR_WEBHOOK_URL", "https://api.clearledgr.com/gmail/push"),
        help="Webhook URL for push notifications",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing setup, don't create resources",
    )
    parser.add_argument(
        "--generate-env",
        action="store_true",
        help="Generate environment configuration",
    )
    
    args = parser.parse_args()
    
    if not args.project_id:
        print("Error: --project-id is required")
        print("Set GOOGLE_CLOUD_PROJECT environment variable or use --project-id flag")
        sys.exit(1)
    
    print("=" * 60)
    print("Clearledgr Gmail Pub/Sub Setup")
    print("=" * 60)
    print(f"Project: {args.project_id}")
    print(f"Webhook: {args.webhook_url}")
    
    if args.generate_env:
        print("\n" + "=" * 60)
        print("Environment Configuration:")
        print("=" * 60)
        print(generate_env_config(args.project_id))
        return
    
    if args.verify_only:
        success = verify_setup(args.project_id)
        sys.exit(0 if success else 1)
    
    # Run setup
    result = setup_pubsub(
        project_id=args.project_id,
        webhook_url=args.webhook_url,
    )
    
    # Verify
    success = verify_setup(args.project_id)
    
    print("\n" + "=" * 60)
    print("Setup Complete!")
    print("=" * 60)
    
    if success:
        print("\n✅ Gmail Pub/Sub is ready for 24/7 processing")
        print("\nNext steps:")
        print("1. Set environment variables (run with --generate-env)")
        print("2. Deploy backend with webhook endpoint")
        print("3. Users authorize via /gmail/authorize endpoint")
        print("4. Gmail will push notifications to your webhook")
    else:
        print("\n❌ Setup incomplete - check errors above")
        sys.exit(1)
    
    print("\nEnvironment config:")
    print(generate_env_config(args.project_id))


if __name__ == "__main__":
    main()
