#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import clean_old_used_videos, clean_old_queue

if __name__ == "__main__":
    clean_old_used_videos(14)
    print("✅ Used videos cleaned (older than 14 days)")
    clean_old_queue(60)
    print("✅ Queue cleaned (processed older than 60 days)")