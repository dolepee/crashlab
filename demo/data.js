window.CRASHLAB_DATA = {
  "contract": "checkout-lost-response",
  "run_id": "compare-1793986335047-0fff6af9",
  "ok": true,
  "buggy": {
    "ok": false,
    "observed": 2,
    "expected": 1,
    "scenario": {
      "matched": true,
      "upstream_status": 200,
      "committed_after_fault": 1,
      "valid": true
    },
    "observations": {
      "first_message": "Could not confirm the order. Try again",
      "second_message": "Order confirmed: #8"
    }
  },
  "control": {
    "ok": true,
    "observed": 2,
    "expected": 2
  },
  "fixed": {
    "ok": true,
    "observed": 1,
    "expected": 1,
    "scenario": {
      "matched": true,
      "upstream_status": 200,
      "committed_after_fault": 1,
      "valid": true
    },
    "observations": {
      "first_message": "Could not confirm the order. Try again",
      "second_message": "Order confirmed: #5"
    }
  }
};
