#!/usr/bin/env python3
"""
Cloud management - removed CloudHealthChecker (v1.3.33+).

Cloud availability is now detected by timeout/connection errors during
actual frame forwarding, not by background health checks.
"""

import logging

logger = logging.getLogger(__name__)


# ============================================================================
# Removed: CloudHealthChecker
# ============================================================================
# CloudHealthChecker was removed in v1.3.33 because:
# 1. Background TCP probes to cloud were potentially detectable
# 2. Simpler architecture - detect cloud issues from actual frame forwarding
# 3. HYBRID mode uses timeout-based detection instead
#
# For HYBRID mode, cloud availability is now determined by:
# - Connection timeout (HYBRID_CONNECT_TIMEOUT)
# - ACK timeout (CLOUD_ACK_TIMEOUT)
# - Connection refused/reset errors
# ============================================================================
