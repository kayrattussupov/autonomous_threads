"""T0.3: fire 200 sequential calls at glm-4.7-flash and measure the 429 rate.

Acceptance (SPEC.md T0.3): < 5% errors. If it fails, the helper roles move to
glm-4.7-flashx (+$0.15/month per SPEC.md T0.3) — that's a one-line change to
config/models.yaml, not a code change.

Run manually: `python -m scripts.check_flash_rate_limit`.
"""
import os
import time

from openai import APIStatusError, OpenAI

N_CALLS = 200


def main() -> None:
    client = OpenAI(base_url="https://api.z.ai/api/paas/v4", api_key=os.environ["GLM_API_KEY"])

    errors = 0
    rate_limit_errors = 0
    for i in range(N_CALLS):
        try:
            client.chat.completions.create(
                model="glm-4.7-flash",
                messages=[{"role": "user", "content": "Ответь одним словом: тест."}],
                max_tokens=10,
            )
        except APIStatusError as exc:
            errors += 1
            if exc.status_code == 429:
                rate_limit_errors += 1
            print(f"call {i}: HTTP {exc.status_code}")
        if i % 20 == 0:
            print(f"{i}/{N_CALLS} done, {errors} errors so far")

    error_rate = errors / N_CALLS
    print(f"\nTotal: {errors}/{N_CALLS} errors ({error_rate:.1%}), of which {rate_limit_errors} were 429.")
    if error_rate < 0.05:
        print("PASS — glm-4.7-flash stays as the helper-role model.")
    else:
        print("FAIL — move lead_scorer/style_critic/classifier to glm-4.7-flashx in config/models.yaml.")


if __name__ == "__main__":
    main()
