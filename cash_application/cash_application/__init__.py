"""Cash application agent for US accounts receivable.

Ingestion reads a remittance, the matcher ranks open invoices, and the
exception agent holds anything that is not a clean full apply. A clerk
confirms before a receipt hits the ledger.
"""

__version__ = "0.1.0"
