import type { ClaimBundle } from "./api";
import { formatClaimType, formatMoney } from "./format";

export function ClaimDetails({ bundle }: Readonly<{ bundle: ClaimBundle }>) {
  const { claim, policy, customer, plan, add_ons: addOns } = bundle;
  const address = customer.address;

  return (
    <div className="claim-details">
      <section className="panel detail-section" aria-labelledby="story-title">
        <p className="eyebrow">Customer account</p>
        <h2 id="story-title">What happened</h2>
        <p className="story">{claim.story}</p>
        <dl className="meta-grid">
          <div>
            <dt>Incident date</dt>
            <dd>{claim.incident_date}</dd>
          </div>
          <div>
            <dt>Location</dt>
            <dd>{claim.incident_location}</dd>
          </div>
          <div>
            <dt>Submitted</dt>
            <dd>{claim.submitted_on}</dd>
          </div>
          <div>
            <dt>Claim type</dt>
            <dd>{formatClaimType(claim.claim_type)}</dd>
          </div>
        </dl>
      </section>

      <section className="panel detail-section" aria-labelledby="items-title">
        <div className="detail-heading">
          <h2 id="items-title">Claimed items</h2>
          <strong className="mono">{formatMoney(claim.amount_claimed)}</strong>
        </div>
        <div className="table-scroll">
          <table className="items-table">
            <thead>
              <tr>
                <th scope="col">Item</th>
                <th scope="col">Category</th>
                <th scope="col">Amount</th>
              </tr>
            </thead>
            <tbody>
              {claim.items.map((item) => (
                <tr key={item.description}>
                  <td data-label="Item">{item.description}</td>
                  <td data-label="Category">
                    {formatClaimType(item.category)}
                  </td>
                  <td data-label="Amount" className="mono money">
                    {formatMoney(item.amount)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section
        className="panel detail-section"
        aria-labelledby="documents-title"
      >
        <h2 id="documents-title">Supporting documents</h2>
        <div className="documents">
          {claim.documents.map((document) => (
            <details key={document.name} open={claim.documents.length === 1}>
              <summary>
                <span>{document.name}</span>
                <span className="document-type">
                  {formatClaimType(document.type)}
                </span>
              </summary>
              <p>{document.content}</p>
            </details>
          ))}
        </div>
      </section>

      <div className="detail-grid">
        <section
          className="panel detail-section"
          aria-labelledby="policy-title"
        >
          <p className="eyebrow">{policy.policy_id}</p>
          <h2 id="policy-title">Policy</h2>
          <dl className="record-list">
            <div>
              <dt>Plan</dt>
              <dd>{plan.name}</dd>
            </div>
            <div>
              <dt>Trip</dt>
              <dd>{policy.trip.destination}</dd>
            </div>
            <div>
              <dt>Trip dates</dt>
              <dd>
                {policy.trip.departs_on} to {policy.trip.returns_on}
              </dd>
            </div>
            <div>
              <dt>Trip cost</dt>
              <dd className="mono">{formatMoney(policy.trip.trip_cost)}</dd>
            </div>
            <div>
              <dt>Travelers</dt>
              <dd>{policy.travelers.join(", ")}</dd>
            </div>
            <div>
              <dt>Deductible</dt>
              <dd className="mono">{formatMoney(plan.deductible)}</dd>
            </div>
            <div>
              <dt>Add-ons</dt>
              <dd>
                {addOns.length
                  ? addOns.map((addOn) => addOn.name).join(", ")
                  : "None"}
              </dd>
            </div>
          </dl>
        </section>

        <section
          className="panel detail-section"
          aria-labelledby="customer-title"
        >
          <p className="eyebrow">{customer.customer_id}</p>
          <h2 id="customer-title">Customer and claim history</h2>
          <dl className="record-list">
            <div>
              <dt>Name</dt>
              <dd>{customer.name}</dd>
            </div>
            <div>
              <dt>Contact</dt>
              <dd>
                {customer.email}
                <br />
                {customer.phone}
              </dd>
            </div>
            <div>
              <dt>Address</dt>
              <dd>
                {address.street}, {address.city}, {address.state} {address.zip}
              </dd>
            </div>
          </dl>
          <h3>Previous claims</h3>
          {customer.previous_claims.length ? (
            <ul className="history-list">
              {customer.previous_claims.map((previous) => (
                <li key={previous.claim_id}>
                  <span className="mono">{previous.claim_id}</span>
                  <span>{previous.summary}</span>
                  <span className="history-outcome">
                    {formatClaimType(previous.outcome)} ·{" "}
                    {formatMoney(previous.amount_paid)}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No previous claims.</p>
          )}
        </section>
      </div>
    </div>
  );
}
