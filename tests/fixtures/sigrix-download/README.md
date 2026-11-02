# A download from Sigrix

`order-desk.zip` is the download Sigrix's own builder writes for an assistant, byte for byte:
`build_assistant_solution_zip` in `src/services/assistant_solution.py` of sigrix-io/sigrix,
at commit `d14e9ac2a`, the function the download route packs with. The assistant is made up
(a pottery shop's order desk). It has one MCP server with one key, one skill of its seller's
own with a reference file, and one knowledge slot with its sample.

Since sigrix-io/sigrix#6561 a download is a solution: `plugin.json` and `assistant.json` sit
beside the files a download always carried (`SKILL.md`, `README.md`, `AGENTS.md`, the MCP
configs and `setup.sh`). `tests/test_install.py` installs it as it is, and in the shape a
download had before #6561, which it makes by taking those additions out again.

To write it again, from a sigrix checkout:

```sh
PYTHONPATH=. python3 make_order_desk.py tests/fixtures/sigrix-download/order-desk.zip
```

where `make_order_desk.py` is:

```python
import hashlib
import sys

from src.services.assistant_solution import IncludedSkill, build_assistant_solution_zip
from src.services.assistant_spec import (
    SETUP_MODE_ONE_STEP,
    ExampleExchange,
    KnowledgeSample,
    KnowledgeSlot,
    McpRequirement,
    ResolvedSkill,
    ToolIntegration,
)
from src.services.skill_reference_rules import SkillReference

SKILL = """---
name: refund-rules
description: Decide whether an order can be refunded, and how much, under the shop's refund limits.
---

Before offering a refund, find the order with find_order and check its date and value against
references/refund-limits.md. Offer refund_order only for an order inside those limits, and say
which limit applies.
"""

LIMITS = b"""# Refund limits

- Orders up to $40: refund without a return, within 14 days of delivery.
- Orders over $40: refund once the item is back, within 30 days of delivery.
- Seconds and gift cards: no refund.
"""

POLICIES = b"""# Policies

## Returns

Unused items in their original packaging can come back within 30 days of delivery.

## Damaged items

Send a photo of the item and its packaging within 14 days of delivery.
"""

reference = SkillReference(name="refund-limits.md", size=len(LIMITS), sha256=hashlib.sha256(LIMITS).hexdigest())
sample = KnowledgeSample(
    extension="md", size=len(POLICIES), sha256=hashlib.sha256(POLICIES).hexdigest(), filename="policies.md"
)
server = McpRequirement(
    name="orders",
    command="uvx",
    args=("shop-orders-mcp==1.4.2",),
    env=("SHOP_API_KEY",),
    tools=("find_order", "refund_order"),
    write_tools=("refund_order",),
    description="Reads the shop's orders and refunds one.",
)
integration = ToolIntegration(
    setup_mode=SETUP_MODE_ONE_STEP,
    skills=(ResolvedSkill(slug="refund-rules", name="refund-rules", description="Refund limits.", found=True),),
    mcp=(server,),
    instructions_md="",
    included_skills=(("refund-rules", "refund-rules"),),
    included_skill_references=(("refund-rules", ("refund-limits.md",)),),
)
data = build_assistant_solution_zip(
    title="Order desk",
    summary="Answers a pottery shop's order questions from its order system and its own policies.",
    prompt=(
        "You answer the questions a pottery shop's customers ask about their orders. Look an order up "
        "before you say anything about it, answer from the shop's policies, and say plainly when they "
        "do not cover a question."
    ),
    tool_integration=integration,
    included_skills=(IncludedSkill(slug="refund-rules", name="refund-rules", markdown=SKILL, references=(reference,)),),
    examples=(ExampleExchange(input="Where is order #2203?", output="It shipped on Monday and is on its way."),),
    knowledge=(
        KnowledgeSlot(
            name="policies",
            description="Your returns, refunds and shipping policies, as you show them to customers.",
            accepts=("md", "txt", "pdf"),
            required=True,
            sample=sample,
        ),
    ),
    samples={"policies": POLICIES},
    skill_reference_files={"refund-rules": ((reference, LIMITS),)},
    listing_id="00000000-0000-4000-8000-000000000001",
)
with open(sys.argv[1], "wb") as out:
    out.write(data)
```
