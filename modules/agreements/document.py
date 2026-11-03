"""The Terms and Conditions stored with a purchase recorded in Discord.

**The source of truth is the website: https://seventhmonth.com/terms/** — the
same Terms a buyer accepts on the website before Stripe checkout, covering both
JULY LEGENDS and Underground (UG). AGREEMENT_FULL_TEXT is a verbatim copy of that
page's text (the version named in its "Last Updated" line).

It is only stored for purchases recorded with `/agreement record` (paid outside
Stripe), where the buyer clicks I Agree in Discord: agreements.agreement_text
keeps the exact text at sign time, so a later edit here never changes what a
past buyer agreed to. Purchases paid on the website have no transcript here —
their consent is collected at checkout.

*** WHEN THE WEBSITE'S TERMS CHANGE, RE-COPY THEM HERE. *** Nothing enforces
it; a divergence means a manual buyer is shown different terms than a website
buyer. Historical rows keep their own stored text and must keep rendering.
"""
from __future__ import annotations

TERMS_URL = "https://seventhmonth.com/terms/"

AGREEMENT_SUMMARY = (
    "By clicking **I Agree** below, you confirm you have read and agree to the Terms "
    f"and Conditions at {TERMS_URL}, including:\n"
    "- Purchases are **one-time** — nothing renews or is charged again automatically.\n"
    "- Membership access is sold in fixed **28-day access periods**; buying partway "
    "through one gets you the rest of that period, at the full price.\n"
    "- All sales are final — payments are **non-refundable** once access or pack "
    "content has been provided.\n"
    "- Leaking, redistribution, reselling or account sharing is prohibited and may "
    "end your access without a refund.\n"
    "- We may share evidence of your purchase and acceptance of these Terms with a "
    "payment processor if a dispute is filed."
)

AGREEMENT_FULL_TEXT = """TERMS AND CONDITIONS
Last Updated: October 31, 2026
Source: https://seventhmonth.com/terms/

These Terms and Conditions (“Terms”) govern purchases of, access to, and use of services offered by JULY LEGENDS and Underground (“UG”), a JULY-affiliated service operating under JULY (collectively, the “Services”; “we,” “us,” and “our” refer to JULY as the operator of these Services). For either JULY LEGENDS or UG, the Services include (a) our fixed-period member-access offering (“Membership Service”), including its member-only communities, files, media, resources, messages, downloads, and other materials; and (b) our one-time digital pack or bundle offering (“Pack Service”), including the files, media, and other digital property delivered as a purchased pack (“Pack Content”).

By purchasing, accessing, downloading, receiving, or using any Service or its content, you (“Member,” “Purchaser,” “Customer,” or “you,” as applicable) acknowledge that you have read, understood, and agreed to these Terms. A person who receives Pack Content from a Purchaser under the limited sharing permission below is a “Recipient.”

These Terms apply equally to corresponding Services, purchases, accounts, and content provided through JULY LEGENDS and UG. The provisions expressly addressing the Membership Service apply only to the Membership Service, and those expressly addressing the Pack Service apply only to the Pack Service. All generally applicable provisions, including restrictions on unauthorized redistribution, investigations, enforcement, intellectual property, and payment disputes, apply to both types of Service under either JULY LEGENDS or UG unless stated otherwise. A purchase from one Service does not, by itself, grant access to another Service or its content.

1. Membership Access Periods

Access to the Membership Service is sold in fixed 28-day access periods (“Access Periods”).

Each Access Period begins and ends on dates established by us. An individual Member’s Access Period does not begin on the date that Member makes a purchase.

A purchase made during an Access Period grants the Member access only for the remainder of the then-current Access Period.

For example, if an Access Period runs from September 1 through September 28, a Member purchasing access on September 15 will receive access from the time access is provided through September 28. The Member does not receive a new 28-day period beginning September 15.

All Members purchasing access to the same Access Period will have their access expire at the end of that Access Period regardless of their individual purchase dates.

The applicable Access Period and, where reasonably practicable, its expiration date will be identified through the Service, checkout process, purchase information, or other materials provided by us.

These fixed 28-day Access Period rules do not apply to purchases of Pack Content.

2. Membership Fixed Pricing and No Proration

The Membership Service purchase price applies to access to the current Access Period as a whole and is not calculated according to the number of days remaining when a Member purchases.

Prices are therefore not prorated, reduced, discounted, credited, or otherwise adjusted solely because a purchase is made after an Access Period has already begun.

A Member purchasing near the end of an Access Period may receive substantially fewer than 28 days of actual access.

By completing a Membership Service purchase after an Access Period has begun, you acknowledge that:

- You are purchasing access only for the time remaining in that Access Period;

- Your access will expire at the same time as the access of other Members participating in that Access Period;

- The full price displayed at checkout still applies; and

- You are not entitled to a partial refund, prorated refund, credit, extension, or discount solely because you purchased after the Access Period began, except where required by applicable law.

3. One-Time Purchases and No Automatic Renewal

Purchases of both Membership Service access and Pack Content are one-time transactions.

A Membership Service purchase does not enroll you in automatic or recurring billing. At the end of an Access Period, your Membership Service access expires unless you separately purchase access for a subsequent Access Period.

A Pack Service purchase is a single, non-recurring purchase of the specified digital pack. A Pack Service purchase does not renew, does not create a Membership Service subscription, and is not subject to the Membership Service’s 28-day Access Period expiration.

We will not automatically charge your payment method for another Access Period or Pack Service purchase unless you separately agree to a recurring billing arrangement in the future under terms clearly disclosed to you.

4. Nature of Membership Access

A Membership Service purchase grants you a limited, personal, revocable, non-exclusive, and non-transferable right to access the Membership Service during the applicable Access Period, subject to these Terms.

Your Membership Service access is for your individual use only. Purchasing Membership Service access does not grant permission to share member-only content with anyone else, including people who are Recipients of Pack Content.

Purchasing access does not transfer ownership of any content, intellectual property, files, media, messages, resources, or other materials made available through the Membership Service.

We reserve the right to modify, replace, add, or remove Membership Service content or features at any time.

5. Pack Service: One-Time Purchase and Limited License

The Pack Service offers digital property in separately purchased packs or bundles. The Pack Service is distinct from the Membership Service.

When payment is completed and the applicable Pack Content is provided, the Purchaser receives a limited, non-exclusive, revocable, non-transferable license to access and use the purchased Pack Content, and to share it only with the number of individual Recipients expressly permitted by the pack purchased, subject to these Terms.

The Purchaser does not acquire ownership of the underlying intellectual property and may not sell, resell, sublicense, publish, or otherwise commercially distribute Pack Content unless we expressly authorize it in writing.

A Pack Service purchase does not grant access to the Membership Service, and a Membership Service purchase does not grant permission to distribute Pack Content beyond the rights attached to a separately purchased pack.

The identity and contents of the pack, its price, and its applicable sharing allowance will be disclosed at or before purchase. Delivery may occur through files, downloads, links, or other methods we authorize. Any hosted access or delivery method remains subject to these Terms.

6. Pack Service: Authorized Sharing Allowances

Only the original Purchaser may initially distribute purchased Pack Content, and only within the allowance of the pack purchased:

- x5 Pack (Small): The Purchaser may provide the purchased Pack Content to a maximum of five (5) distinct individual Recipients.

- x15 Pack (Large): The Purchaser may provide the purchased Pack Content to a maximum of fifteen (15) distinct individual Recipients.

These limits refer to five or fifteen separate recipient individuals, respectively, in addition to the original Purchaser’s own use. The allowance is a cumulative limit per purchased pack, not a rotating number of available sharing slots. Sending the content to a new individual after it was already provided to the maximum number of Recipients is not permitted, even if a previous Recipient deletes the content or stops using it.

Providing a copy, file, download, or access link to an individual counts as sharing with that individual. Sharing with a group, server, channel, organization, publicly accessible folder, or other location where the number or identity of recipients cannot be reliably limited to the authorized individuals is prohibited.

The sharing allowance applies only to the specific Pack Content purchased. It does not authorize the Purchaser or any Recipient to share Membership Service content, to redistribute other packs, or to grant access to other JULY LEGENDS or UG materials.

A Pack Service sharing allowance is not permission for any Recipient to pass the Pack Content to another person. There is no second level of permitted distribution.

7. Pack Service: Recipient Restrictions and Purchaser Responsibility

The Purchaser is responsible for selecting authorized Recipients, controlling how Pack Content is delivered to them, and taking reasonable steps to prevent unauthorized access, onward sharing, leaks, or redistribution.

Before sharing Pack Content, the Purchaser must clearly inform each Recipient that the material is restricted, may be used only by that Recipient, and must not be forwarded, copied for another person, resold, uploaded, published, reposted, shared with additional persons, or otherwise redistributed without our express written permission. The Purchaser must obtain each Recipient’s agreement to those restrictions before supplying the content.

Recipients receive permission only to access and use the Pack Content provided to them within the Purchaser’s allowance. They receive no right to share it with anyone else, even if sharing is free of charge, privately, or with just one additional person.

The original Purchaser is responsible under these Terms for unauthorized sharing or redistribution by their Recipients, including when a Recipient shares the material without the Purchaser’s instruction, approval, or knowledge, to the fullest extent permitted by applicable law. A Purchaser’s claim that a Recipient—not the Purchaser—committed the leak does not automatically prevent enforcement against the Purchaser. We will consider reasonably available information when deciding whether a violation is attributable to that Purchaser’s distribution and what enforcement action is appropriate.

Purchasers must not deliberately use a Recipient, intermediary, alternate account, or third party to avoid the sharing allowance or these restrictions. If the Purchaser discovers or reasonably suspects unauthorized onward sharing, the Purchaser must promptly take reasonable steps to stop further sharing and notify us using the contact information below.

Unauthorized sharing by a Recipient, sharing beyond the pack’s numerical allowance, or failure to comply with the obligations in this section may constitute a material breach of these Terms and may result in the same investigation, suspension, termination, and refund consequences that apply to unauthorized redistribution of Membership Service content, as set out below.

8. Payments

Payments must be made using the payment methods officially provided or approved by us at checkout.

You are responsible for providing accurate payment information and for any fees or charges associated with your chosen payment method.

The price, currency, purchased Service or pack, and other material purchase information (including the applicable Membership Service Access Period or Pack Service sharing limit) will be displayed or otherwise communicated before or in connection with purchase.

9. Refund Policy

ALL SALES ARE FINAL AND PAYMENTS ARE NON-REFUNDABLE ONCE MEMBERSHIP SERVICE ACCESS OR PURCHASED PACK CONTENT HAS BEEN PROVIDED, TO THE FULLEST EXTENT PERMITTED BY APPLICABLE LAW.

Because purchases provide access to digital content, member-only communities, resources, files, or other digital materials, we generally do not provide refunds after the purchased access or content has been delivered or made available.

Purchasing Membership Service access after an Access Period has already begun does not create an entitlement to a full or partial refund, prorated refund, credit, discount, or extension solely because less than 28 days remain in that Access Period.

Suspension, restriction, or termination resulting from a violation of these Terms does not create an entitlement to a full or partial refund. This applies equally to Pack Service purchases, including violations by the Purchaser or unauthorized onward redistribution by a Recipient attributable to the Purchaser under Section 7.

This includes termination resulting from unauthorized distribution, leaking, exceeding an authorized pack sharing limit, reselling, account sharing, intellectual-property infringement, circumvention of access restrictions, fraud, abuse, or other prohibited conduct.

Nothing in this section limits any rights that cannot legally be waived under applicable law or any rights that a payment processor, card issuer, bank, or other financial institution is independently required to provide.

10. Strict Prohibition on Leaking and Unauthorized Redistribution

Membership Service content is intended exclusively for authorized Members. Pack Content is intended exclusively for its original Purchaser and the limited number of Recipients authorized under Sections 6 and 7.

The limited sharing permission for Pack Content is the only exception to the prohibitions on sharing below. Unless these Terms expressly permit it or we provide express written permission, you may not:

- Copy, reproduce, record, screenshot, screen-record, republish, upload, distribute, transmit, sell, trade, sublicense, or otherwise share content outside its authorized use;

- Provide another person with access to Membership Service content or provide Pack Content to a person who is not an authorized Recipient;

- Share, sell, lend, or transfer your account or login credentials;

- Post restricted content to social media, file-sharing services, messaging groups, forums, websites, servers, archives, or any other public or private location not expressly authorized for its distribution;

- Assist or encourage another person to obtain, distribute, or preserve unauthorized copies of restricted content; or

- Attempt to bypass technical measures designed to prevent copying, redistribution, or unauthorized access.

For avoidance of doubt, permitted delivery of Pack Content by the original Purchaser to the applicable five or fifteen authorized individuals is not an unauthorized distribution; any onward distribution by a Recipient or sharing beyond the pack allowance is prohibited.

Unauthorized redistribution constitutes a material breach of these Terms.

11. Investigation of Suspected Violations

We reserve the right to investigate suspected violations of these Terms concerning either Service using information reasonably available to us.

This may include account activity, access and download records, timestamps, identifying markers, reports, screenshots, platform information, technical records, communications, and other relevant information, including information reasonably connecting improperly redistributed Pack Content to a Purchaser or Recipient.

We are not required to publicly disclose or provide a Member or Purchaser with our confidential internal evidence, investigative techniques, sources, detection methods, security measures, or information concerning other users as a condition of suspending or terminating access or a pack license, except where required by law.

Where disclosure is required by applicable law, a court, payment processor, financial institution, or other authorized party, we may provide relevant information directly to that party.

12. Suspension and Termination

We may suspend, restrict, or permanently terminate a Member’s or Purchaser’s access to the Services or rights to use Pack Content where, based on information reasonably available to us, we determine that the Member or Purchaser has violated these Terms or poses a material risk to the Services, their content, other Members or Recipients, or our intellectual property.

Serious violations, including unauthorized distribution or leaking of Membership Service content, sharing Pack Content beyond the permitted allowance, or unauthorized onward redistribution of Pack Content by a Recipient attributable to the Purchaser, may result in immediate termination without prior warning.

A violation involving Pack Content may result in revocation of the Purchaser’s Pack Service license, disabling of available download or delivery access, and suspension or termination of other JULY LEGENDS or UG service access held by the same Purchaser. If the Pack Service license is terminated, the Purchaser must cease use and distribution of the Pack Content and instruct their Recipients to do the same and delete copies under their control, to the extent permitted by applicable law.

Our determination regarding continued access to the Services is final for purposes of our Services, subject to any rights that cannot legally be waived.

We are not obligated to provide advance notice, disclose confidential evidence, reveal investigative methods, provide repeated warnings, or offer an internal appeal before terminating access for a material violation, except where required by law.

Termination for misconduct does not erase the Purchaser’s obligations under these Terms and does not, by itself, entitle the Purchaser to a refund.

13. Payment Disputes and Chargebacks

Members and Purchasers retain any dispute or chargeback rights that cannot legally be waived and any rights independently provided by their card issuer, bank, payment processor, or other financial institution.

However, you agree not to knowingly make false, misleading, or fraudulent statements to a card issuer, bank, payment processor, or other financial institution in an attempt to recover payment after receiving the purchased access or Pack Content.

In particular, termination of access or a Pack Service license for violating these Terms does not mean that the originally purchased access or Pack Content was never provided.

If a payment dispute, chargeback, refund claim, or similar proceeding is initiated, we reserve the right to provide relevant information to the applicable payment processor or financial institution, including evidence concerning:

- The purchase;

- Acceptance of these Terms;

- The Access Period or specific pack purchased;

- The date access or Pack Content was provided;

- Account or download access;

- Content delivery;

- The applicable pack sharing allowance;

- Account activity;

- Communications;

- Violations of these Terms; and

- Suspension or termination.

A payment dispute does not require us to restore access or a license that has otherwise been properly suspended or terminated.

Knowingly submitting a false or materially misleading payment dispute may constitute an additional violation of these Terms.

14. Evidence of Delivery and Access

For Membership Service purchases, access is considered provided when the Member’s account is granted access to the applicable member-only Service, platform, community, files, content, or other purchased materials.

For Pack Service purchases, Pack Content is considered provided when the purchased files or content have been delivered to the Purchaser or made accessible to the Purchaser through an authorized download, delivery link, account, or other designated delivery method.

We may maintain records reasonably demonstrating purchase, delivery, and access, including timestamps, access and download records, transaction records, account activity, and other technical information.

These records may be retained and provided to payment processors, banks, card issuers, financial institutions, or other authorized parties where reasonably necessary to respond to a dispute or protect our legitimate interests, subject to applicable law.

15. Intellectual Property

Unless expressly stated otherwise, all content provided through the Services, including Pack Content, remains the property of JULY or its respective rights holders.

Your purchase grants a limited right to use Membership Service content during the applicable Access Period or Pack Content under the limited pack license, as applicable, but does not grant you ownership of that content.

No license is granted to reproduce, distribute, publicly display, sell, sublicense, commercially exploit, or create unauthorized derivative copies of content except where expressly permitted in Sections 5–7 for Pack Content or otherwise authorized by us in writing.

Any rights not expressly granted under these Terms are reserved.

16. Account and Purchaser Responsibility

You are responsible for activity occurring through your account and for maintaining the confidentiality of your login credentials.

You must notify us promptly if you believe your account has been compromised.

A claim that another person used your account does not automatically prevent us from restricting or terminating the account where it was involved in unauthorized distribution or other prohibited activity.

For Pack Service purchases, the original Purchaser remains responsible for complying with recipient limits, delivery restrictions, and Recipient obligations as described in Sections 6 and 7.

We may consider the circumstances and available information when determining the appropriate action.

17. Expiration of Membership Access

Membership Service access automatically expires at the end of the applicable Access Period.

Expiration of an Access Period is not a cancellation, suspension, or termination for misconduct. It is simply the scheduled conclusion of the membership access that was purchased.

Continued Membership Service access during a subsequent Access Period requires a separate purchase.

Purchasing access to one Access Period does not guarantee availability, pricing, features, or eligibility for any future Access Period.

These expiration rules do not apply to Pack Content solely because a 28-day Membership Service Access Period has ended. Pack Content remains subject to its license terms and any lawful suspension or termination for misconduct.

18. No Guarantee of Continued Access Following Misconduct

Access and use of the Services remain conditional upon continued compliance with these Terms.

Payment does not guarantee continued access or use rights regardless of conduct.

A Member who materially violates these Terms may lose Membership Service access for the remainder of the applicable Access Period without reimbursement. A Pack Service Purchaser who materially violates these Terms may lose their Pack Service license and access to any available hosted Pack Content without reimbursement. Both consequences are subject to any rights required by applicable law.

19. Enforcement

Our failure to immediately enforce any provision of these Terms does not waive our right to enforce that provision later.

We may take different enforcement actions depending on the circumstances, severity of the conduct, available evidence, prior conduct, and risk presented by a particular violation.

The fact that one Member or Purchaser receives a warning or different enforcement action does not create an entitlement for another Member or Purchaser to receive the same treatment.

20. Limitation of Liability

To the fullest extent permitted by applicable law, JULY and its owners, operators, employees, contractors, and representatives will not be liable for indirect, incidental, special, consequential, or punitive damages arising from access to, inability to access, suspension from, termination from, or expiration of the Services, or from the delivery, use, restricted distribution, suspension, or termination of rights concerning Pack Content.

Nothing in these Terms excludes or limits liability that cannot lawfully be excluded or limited.

21. Changes to These Terms

We may update these Terms from time to time.

Material changes will take effect as permitted by applicable law and, where appropriate, after notice is provided to Members or Purchasers.

The Terms applicable to a purchase will generally be the version presented or made available in connection with that purchase, subject to changes that may lawfully apply to ongoing use of the Services or Pack Content.

22. Governing Law

JULY LEGENDS: With respect to purchases of, access to, and use of services and content provided by JULY LEGENDS, these Terms will be governed by the laws of the State of New York, United States of America, without regard to its conflict-of-law principles.

Underground (UG): With respect to purchases of, access to, and use of services and content provided by Underground (UG), these Terms will be governed by the laws of the State of Kuwait, without regard to its conflict-of-law principles.

This allocation applies to the Membership Service and Pack Service offered by each respective service, including disputes concerning payments, content sharing, access restrictions, and enforcement. The affiliation of UG with JULY does not, by itself, change the governing law applicable to UG services. If a matter concerns both JULY LEGENDS and UG, the respective governing law applies to the portion of the matter arising from each service, to the extent legally permissible.

Nothing in this section excludes or limits mandatory consumer-protection rights or other laws that cannot lawfully be waived or displaced by a choice-of-law provision.

23. Contact

Questions concerning these Terms may be directed to:

JULY

lai@seventhmonth.com

cynic@seventhmonth.com

DIVINE UNDERGROUND

yun@seventhmonth.com

BY PURCHASING OR ACCESSING A SERVICE OR PACK CONTENT, YOU ACKNOWLEDGE THAT YOU HAVE READ AND AGREED TO THESE TERMS, INCLUDING THE FIXED MEMBERSHIP ACCESS-PERIOD RULES, NON-PRORATED MEMBERSHIP PRICING, PACK SHARING LIMITS, PURCHASER RESPONSIBILITY FOR RECIPIENTS, REFUND POLICY, RESTRICTIONS ON REDISTRIBUTION, AND OUR RIGHT TO TERMINATE ACCESS OR PACK-USE RIGHTS FOR VIOLATIONS."""


__all__ = ["AGREEMENT_FULL_TEXT", "AGREEMENT_SUMMARY", "TERMS_URL"]
