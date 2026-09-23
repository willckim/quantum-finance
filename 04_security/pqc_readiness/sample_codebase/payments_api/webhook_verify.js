// SampleCo Finance -- verifies webhooks from the synthetic payment processor.
const crypto = require("crypto");
const jwt = require("jsonwebtoken");

function payloadFingerprint(body) {
  // Used as a de-duplication key for incoming webhook bodies.
  return crypto.createHash("md5").update(body).digest("hex");
}

function verifyProcessorToken(token, processorPublicKey) {
  return jwt.verify(token, processorPublicKey, { algorithms: ["RS256", "EdDSA"] });
}

module.exports = { payloadFingerprint, verifyProcessorToken };
