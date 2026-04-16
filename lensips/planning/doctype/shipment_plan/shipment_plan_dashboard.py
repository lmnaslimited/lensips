from frappe import _


def get_data(data=None):

    return {
        'fieldname': 'shipment_plan',
        'transactions': [
            {
                'label': _('Fulfillment'),
                'items': ['Pick List']
                # 'items': ['Pick List', 'Stock Entry', 'Delivery Note']
            },
            {
                'label': _('Transportation'),
                'items': ['Shipment']
            }
        ]
    }
