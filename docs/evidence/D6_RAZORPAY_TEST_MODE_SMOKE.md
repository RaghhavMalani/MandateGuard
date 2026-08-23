# D6 Razorpay Test Mode Smoke Evidence

Date: 2026-08-23
Execution implementation commit:
644af38a09ffe3dba762e764433fdeda420a1f3f

## Result

MandateGuard final action: ALLOW

Transaction SHA-256:
d4264d83cddddfb41760fc2b8cde51fa4be5c0eeecb98fbcfbe2f9edf72e3a27

Execution request SHA-256:
ec94931668c6441c0bbd5ca607816cdfa8f8c7b824d6b1672fd4598a29aec374

Receipt:
mg_32c9a086a2f711e23202e1edef9a3e579521f

Razorpay Test Mode Order ID:
order_TTDWvsqewTGD4G

Status:
created

Amount:
100 INR minor units (₹1.00)

Currency:
INR

## Claim

This run demonstrates that an ALLOW authorization produced a real Razorpay
Test Mode Order through MandateGuard's signed execution-capability path.

The execution gate re-derived the historical authorization context before
issuance, verified the signed capability, recomputed the transaction hash,
rebuilt the exact Razorpay request, recomputed its request hash, atomically
reserved the execution nonce, and only then invoked the Razorpay Orders API.

## Non-claims

This does not demonstrate:
- a customer payment;
- payment authorization;
- payment capture;
- settlement;
- real-money movement.

Only Razorpay Test Mode Order creation was performed.

No Razorpay credentials or MandateGuard HMAC secrets are recorded here.