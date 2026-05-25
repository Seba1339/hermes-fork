#!/usr/bin/env python3
from hermes_enhanced.coding import code_quality_gate
r = code_quality_gate('hermes_enhanced/__init__.py')
print(r.summary())
