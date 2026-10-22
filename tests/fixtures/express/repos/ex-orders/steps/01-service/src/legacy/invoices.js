const express = require("express");
const { getInvoice, listInvoices } = require("../controllers/invoices");

const router = express.Router();

router.get("/", listInvoices);
router.get("/:invoiceId", getInvoice);

module.exports = router;
