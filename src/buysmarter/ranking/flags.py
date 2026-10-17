"""Con texts one ranking stage writes and a later stage matches on (role bars, rewrites)."""

# The con carried by a candidate flagged for a suspiciously low price. Also used
# to exclude such candidates from every alternative role (they stay in the
# ranked table with the con visible, but are never endorsed as a "best X").
SUSPICIOUS_PRICE_CON = "подозрительно низкая цена для категории"
# A same-product dedupe finding a much pricier duplicate elsewhere is evidence
# on its own — brand tier is irrelevant, so the con names the gap directly
# rather than reusing the generic unknown-brand wording. Only bites when the
# candidate itself is priced BELOW the market reference: at/above it, the
# pricier duplicate is the outlier (an overpriced listing), not this one.
DUP_PRICE_GAP_MARKER = "ниже другого листинга этого же товара"
DUP_PRICE_GAP_CON = "цена в {gap:.1f} раза " + DUP_PRICE_GAP_MARKER + " — проверьте продавца"
# A title mimicking a known brand without a genuine match (e.g. spaced-out
# "J BL" for JBL) is a counterfeit signal independent of price.
MIMIC_BRAND_CON = "возможная подделка под {brand}"

UNVERIFIED_FEATURE_CON = "не удалось проверить: {feature}"

COUNTERFEIT_CON = "отзывы подозревают подделку"
COUNTERFEIT_CON_MULTI = (
    "в отзывах жалобы на подделку у части продавцов — выбирайте продавца с высоким рейтингом"
)
