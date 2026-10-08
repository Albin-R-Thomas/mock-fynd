import React from "react";
import { Sparkles } from "lucide-react";

export default function MockStoreSummary({ store }) {
  const waiting = store.open + store.packed;
  const active = waiting + store.out_for_delivery;
  const deliveryShare = store.total
    ? Math.round((store.delivered / store.total) * 100)
    : 0;
  const recommendation =
    store.delayed_deliveries > 0
      ? `Prioritize ${store.delayed_deliveries} delayed ${store.delayed_deliveries === 1 ? "delivery" : "deliveries"}. Check courier availability and follow up on these orders first.`
      : waiting >= 30
        ? `${waiting} orders are waiting to be packed or dispatched. Add packing capacity and clear the oldest orders first.`
        : active > 0
          ? `Keep ${store.open} open orders moving into packing and prepare ${store.packed} packed orders for dispatch. No delivery delays are flagged in this snapshot.`
          : "There are no active orders in this snapshot. The team can review completed deliveries and prepare for the next order wave.";
  return (
    <section className="mock-ai-summary" aria-label="Mock AI summary">
      <div>
        <h3>
          <Sparkles size={16} />
          AI store summary
        </h3>
        <span className="badge purple">MOCK</span>
      </div>
      <p>
        {store.total
          ? `${active} of ${store.total} retained orders are in fulfilment, with ${deliveryShare}% delivered.`
          : "This store has no retained orders yet."}{" "}
        {store.returned > 0 &&
          `${store.returned} ${store.returned === 1 ? "order has" : "orders have"} been returned.`}
      </p>
      <strong>Suggested next step</strong>
      <p>{recommendation}</p>
      <small>Illustrative summary based on this store snapshot.</small>
    </section>
  );
}
