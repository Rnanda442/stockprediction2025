const people = [
  { initials: "GC", name: "Gargi", color: "sage", amount: 1040, note: "4 of 5 deposits", percent: 80 },
  { initials: "AM", name: "Ari", color: "peach", amount: 820, note: "3 of 5 deposits", percent: 63 },
  { initials: "JR", name: "Jordan", color: "blue", amount: 780, note: "3 of 5 deposits", percent: 60 },
  { initials: "SL", name: "Sam", color: "gold", amount: 780, note: "3 of 5 deposits", percent: 60 },
];

const budget = [
  { name: "Flights", color: "#f19a77", planned: 1624, booked: 1624, status: "Booked" },
  { name: "Apartment", color: "#c4b5dc", planned: 1680, booked: 1680, status: "50% paid" },
  { name: "Activities", color: "#9fd1bd", planned: 760, booked: 0, status: "Planning" },
  { name: "Food", color: "#e9d19d", planned: 720, booked: 0, status: "Planning" },
  { name: "Transit + buffer", color: "#bad8df", planned: 416, booked: 0, status: "Planning" },
];

const bookings = [
  { icon: "✈", color: "coral", label: "Booked", title: "Round-trip flights", text: "TAP Air Portugal · Jun 18 - Jun 24", cost: "$1,624", rule: "Nonrefundable" },
  { icon: "⌂", color: "lilac", label: "Booked", title: "Alfama apartment", text: "3 bedrooms · 6 nights · Sleeps 4", cost: "$1,680", rule: "50% refundable" },
  { icon: "♢", color: "mint", label: "Vote open", title: "Sintra day trip", text: "Train, guided tour, and lunch", cost: "$312", rule: "3 of 4 voted", vote: true },
  { icon: "+", color: "sand", label: "Idea", title: "Sunset sail", text: "Two-hour small-group sailing cruise", cost: "$196", rule: "Needs discussion", vote: true },
];

const currency = (value) => `$${value.toLocaleString("en-US")}`;

function renderPeople() {
  const rows = people
    .map(
      (person) => `
        <div class="person-row">
          <span class="avatar avatar-${person.color}">${person.initials}</span>
          <div><p>${person.name}</p><small>${person.note}</small></div>
          <strong>${currency(person.amount)}</strong>
        </div>`
    )
    .join("");
  document.querySelector("#overview-contributors").innerHTML = rows;

  document.querySelector("#contribution-people").innerHTML = people
    .map(
      (person) => `
        <div class="person-row">
          <span class="avatar avatar-${person.color}">${person.initials}</span>
          <div><p>${person.name}</p><small>${person.note}</small></div>
          <div class="person-progress"><span style="width:${person.percent}%"></span></div>
          <strong>${currency(person.amount)} <small>of $1,300</small></strong>
        </div>`
    )
    .join("");
}

function renderBudget() {
  document.querySelector("#budget-rows").innerHTML = budget
    .map(
      (item) => `
        <div class="budget-row">
          <span class="category-name"><i class="category-dot" style="background:${item.color}"></i>${item.name}</span>
          <span>${currency(item.planned)}</span>
          <span>${item.booked ? currency(item.booked) : "—"}</span>
          <span class="status ${item.status === "Planning" ? "status-planning" : "status-booked"}">${item.status}</span>
        </div>`
    )
    .join("");
}

function renderBookings() {
  document.querySelector("#booking-grid").innerHTML = bookings
    .map(
      (booking) => `
        <article class="booking-card">
          <div class="booking-card-top">
            <span class="stat-icon icon-${booking.color}">${booking.icon}</span>
            <span class="pill ${booking.vote ? "pill-sand" : "pill-mint"}">${booking.label}</span>
          </div>
          <h3>${booking.title}</h3>
          <p>${booking.text}</p>
          <div class="booking-meta">
            <span>Estimated cost<strong>${booking.cost}</strong></span>
            ${
              booking.vote
                ? `<button class="vote-button" data-action="vote">Vote now</button>`
                : `<span>Refund rule<strong>${booking.rule}</strong></span>`
            }
          </div>
        </article>`
    )
    .join("");
}

function showView(viewName) {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
  document.querySelector(`#${viewName}`).classList.add("active");
  document.querySelector(`.nav-item[data-view="${viewName}"]`)?.classList.add("active");
  document.querySelector("#page-title").textContent = viewName[0].toUpperCase() + viewName.slice(1);
}

let toastTimer;
function toast(message) {
  const element = document.querySelector("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 2400);
}

const messages = {
  remind: "Reminder queued for Ari, Jordan, and Sam.",
  "add-budget": "Expense form would open here.",
  "record-payment": "Payment confirmation form would open here.",
  "add-booking": "Booking form would open here.",
  export: "Settlement summary exported.",
  vote: "Vote recorded. The group can see your choice.",
};

document.addEventListener("click", (event) => {
  const nav = event.target.closest("[data-view]");
  const target = event.target.closest("[data-view-target]");
  const action = event.target.closest("[data-action]");
  if (nav) showView(nav.dataset.view);
  if (target) showView(target.dataset.viewTarget);
  if (action) toast(messages[action.dataset.action]);
});

renderPeople();
renderBudget();
renderBookings();
