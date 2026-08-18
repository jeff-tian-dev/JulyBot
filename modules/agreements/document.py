"""The purchase agreement content shown by /agreement send.

The full text is too long for a Discord embed (4096 char cap) or a modal
TextDisplay (4000 char cap), so the buyer-facing display is the attached PDF
itself plus a short summary embed. AGREEMENT_FULL_TEXT is a verbatim transcript
of that PDF, stored per-row in agreements.agreement_text at send time so the DB
record captures the exact terms text even if the PDF file changes later.
"""
from __future__ import annotations

from pathlib import Path

AGREEMENT_PDF_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "agreements" / "terms_and_conditions.pdf"

AGREEMENT_SUMMARY = (
    "By clicking **I Agree** below, you confirm you have read and agree to the attached "
    "Terms and Conditions, including:\n"
    "- All sales are final — payments are **non-refundable** once access is provided.\n"
    "- Content leaking, redistribution, or account sharing is prohibited and may result "
    "in termination without a refund.\n"
    "- You agree not to make false or misleading statements to PayPal or another payment "
    "processor to recover payment after receiving access.\n"
    "- We may share evidence of your purchase and acceptance of these Terms with a "
    "payment processor if a dispute is filed.\n\n"
    "Read the full Terms and Conditions in the attached PDF before agreeing."
)

AGREEMENT_FULL_TEXT = """TERMS AND CONDITIONS
Last Updated: 8/17/2026

These Terms and Conditions ("Terms") govern access to and use of the [JULY LEGENDS SUBSCRIPTION] ("Service," "we," "us," or "our"), including all subscription-only content, communities, files, media, resources, messages, downloads, and other materials made available through the Service.

By purchasing a subscription, accessing the Service, or otherwise using any subscriber-only content, you ("Member," "Subscriber," or "you") acknowledge that you have read, understood, and agreed to these Terms.

1. Subscription Access

A subscription grants you a limited, personal, revocable, non-exclusive, and non-transferable right to access the Service for the duration of your active subscription.

Your subscription is for your individual use only.

Purchasing a subscription does not transfer ownership of any content, intellectual property, files, media, or other materials provided through the Service.

We reserve the right to modify, replace, add, or remove content or features of the Service at any time.

2. Payments

Payments must be made using the payment methods officially provided or approved by us at checkout.

You are responsible for providing accurate payment information and for any fees or charges associated with your chosen payment method. (e.g. Paypal Goods and Services Fees)

3. Refund Policy

ALL SALES ARE FINAL AND PAYMENTS ARE NON-REFUNDABLE ONCE ACCESS TO THE SERVICE HAS BEEN PROVIDED, TO THE FULLEST EXTENT PERMITTED BY APPLICABLE LAW.

Because the Service provides access to digital content and subscriber-only materials, removal, suspension, or termination of access due to a violation of these Terms does not create an entitlement to a full or partial refund.

This includes termination resulting from content leaking, unauthorized distribution, reselling, account sharing, copyright infringement, circumvention of access restrictions, fraud, abuse, or any other prohibited conduct.

We do not provide prorated refunds for unused subscription time following a suspension or termination caused by a Member's violation of these Terms.

Nothing in this section limits any non-waivable rights you may have under applicable law or any rights that a payment processor is independently required to provide under its own governing terms.

4. Strict Prohibition on Leaking and Redistribution

Subscriber-only content is confidential and intended exclusively for authorized Members.

You may not, without our express written permission:
1. Copy, reproduce, record, screenshot, screen-record, download, republish, upload, distribute, transmit, sell, trade, sublicense, or otherwise share subscriber-only content outside the authorized Service;
2. Provide another person with access to content obtained through your subscription;
3. Share, sell, lend, or transfer your account or login credentials;
4. Post subscriber-only content to social media, file-sharing services, messaging groups, forums, websites, servers, archives, or any other public or private location;
5. Assist or encourage another person to obtain, distribute, or preserve unauthorized copies of subscriber-only content;
6. Attempt to bypass technical measures designed to prevent copying, redistribution, or unauthorized access.

Unauthorized redistribution constitutes a material breach of these Terms.

5. Investigation of Suspected Violations

We reserve the right to investigate suspected violations of these Terms using information reasonably available to us, including account activity, mutual friends, access records, timestamps, identifying markers, reports, screenshots, platform information, technical records, and other relevant information.

We are not required to publicly disclose or provide a Member with our internal evidence, investigative techniques, sources, detection methods, security measures, or information concerning other users as a condition of suspending or terminating access.

Where disclosure is required by applicable law, a court, a payment processor, or another authorized third party, we may provide relevant information directly to that party.

6. Suspension and Termination

We may suspend, restrict, or permanently terminate a Member's access where, in our determination based on the information reasonably available to us, the Member has violated these Terms or poses a material risk to the Service, its content, its Members, or its intellectual property.

Serious violations, including unauthorized distribution or leaking of subscriber-only content, may result in immediate termination without prior warning.

Our decision regarding continued access to the Service is final for purposes of our Service, subject to any rights that cannot legally be waived.

We are not obligated to provide advance notice, disclose confidential evidence, reveal investigative methods, provide repeated warnings, or offer an internal appeal before terminating access for a material violation.

Termination for misconduct does not erase the Member's obligations under these Terms and does not entitle the Member to a refund.

7. Payment Disputes, Chargebacks, and PayPal Claims

Members retain any dispute rights that cannot legally be waived and any rights independently provided by their payment provider.

However, you agree that you will not knowingly make false, misleading, or fraudulent statements to PayPal, a card issuer, bank, payment processor, or other financial institution in an attempt to recover payment after receiving access to the Service.

In particular, termination of your account for violating these Terms does not mean that the Service was not delivered.

If a payment dispute, chargeback, refund claim, or similar proceeding is initiated, we reserve the right to provide the payment processor or financial institution with information relevant to the transaction, including evidence of purchase, acceptance of these Terms, account access, content delivery, account activity, violations, communications, and termination.

A payment dispute does not require us to restore a suspended or terminated account.

Knowingly submitting a false or materially misleading payment dispute may constitute an additional violation of these Terms.

8. Evidence of Delivery and Access

For digital subscriptions, access is considered provided when the Member's account is granted access to the subscriber-only Service, content, platform, community, files, or other purchased materials.

We may maintain records demonstrating delivery and access, including timestamps, login records, access logs, transaction records, and other technical information.

These records may be retained and supplied to payment processors where reasonably necessary to respond to a payment dispute or protect our legitimate interests.

9. Intellectual Property

Unless expressly stated otherwise, all content provided through the Service remains the property of [JULY] or its respective rights holders.

Your subscription provides access to the content but does not grant you ownership of it.

No license is granted to reproduce, distribute, publicly display, sell, sublicense, commercially exploit, or create unauthorized derivative copies of subscriber-only content.

Any rights not expressly granted under these Terms are reserved.

10. Account Responsibility

You are responsible for activity occurring through your account and for maintaining the confidentiality of your login credentials.

You must notify us promptly if you believe your account has been compromised.

Claims that another person used your account do not automatically prevent us from restricting the account where the account was involved in unauthorized distribution or other prohibited activity. We may consider the circumstances and available information when determining the appropriate action.

11. Subscription Cancellation

Where recurring billing is offered, you may cancel future renewal of your subscription in accordance with the cancellation method provided by us or the applicable payment provider.

Cancellation prevents future renewals but does not ordinarily create a right to a refund for a subscription period that has already begun or for digital access already provided.

12. No Guarantee of Permanent Access

Subscription access is conditional upon continued compliance with these Terms.

Payment for a subscription does not guarantee access regardless of conduct. A Member who violates these Terms may lose access for the remainder of a paid subscription period without reimbursement, except where otherwise required by applicable law.

13. Enforcement

Our failure to immediately enforce any provision of these Terms does not waive our right to enforce that provision later.

We may take different enforcement actions depending on the circumstances, severity, available evidence, prior conduct, and risk presented by a particular violation.

The fact that one Member receives a warning or different enforcement action does not create an entitlement for another Member to receive the same treatment.

14. Limitation of Liability

To the fullest extent permitted by applicable law, [JULY] and its owners, operators, employees, and representatives will not be liable for indirect, incidental, special, consequential, or punitive damages arising from access to, inability to access, suspension from, or termination from the Service.

Nothing in these Terms excludes liability that cannot lawfully be excluded.

15. Changes to These Terms

We may update these Terms from time to time.

Material changes will take effect as permitted by applicable law and, where appropriate, after notice is provided to Members.

Continued use of the Service following the effective date of updated Terms constitutes acceptance of those Terms where permitted by applicable law.

16. Governing Law

These Terms will be governed by the laws of [THE STATE OF NEW YORK, UNITED STATES OF AMERICA], without regard to conflict-of-law principles, except where applicable consumer-protection law requires otherwise.

17. Contact

Questions concerning these Terms may be directed to:
[JULY]
[seventhmonthclash@gmail.com]

BY PURCHASING OR ACCESSING THE SERVICE, YOU ACKNOWLEDGE THAT YOU HAVE READ AND AGREED TO THESE TERMS, INCLUDING THE RESTRICTIONS ON REDISTRIBUTION, THE REFUND POLICY, AND OUR RIGHT TO TERMINATE ACCESS FOR VIOLATIONS."""


__all__ = ["AGREEMENT_PDF_PATH", "AGREEMENT_SUMMARY", "AGREEMENT_FULL_TEXT"]
