const express = require("express");
const invoices = require("./invoices");

const router = express.Router();

router.use("/invoices", invoices);

module.exports = router;
