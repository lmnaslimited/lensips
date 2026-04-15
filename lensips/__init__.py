__version__ = "0.0.1"

import erpnext.stock.doctype.bin.bin as bin_module
from erpnext.stock.doctype.bin.bin import Bin # Import the Class
from lensips.patches.v0_0.bin_logic import (
    custom_update_qty, 
    custom_get_bin_details, 
    custom_set_projected_qty
)
import erpnext.stock.report.stock_projected_qty.stock_projected_qty as report_module
from lensips.patches.v0_0.stock_projected_report import custom_execute
#Auto Reorder Override
import erpnext.stock.reorder_item
from lensips.patches.v0_0.reorder import custom_create_material_request

# 1. Patching standalone functions in the module
bin_module.update_qty = custom_update_qty
bin_module.get_bin_details = custom_get_bin_details

# 2. Patching the Method inside the Class (Crucial Fix)
Bin.set_projected_qty = custom_set_projected_qty

# Uncomment to get the Reserved qty for material requests
report_module.execute = custom_execute

erpnext.stock.reorder_item.create_material_request = custom_create_material_request
