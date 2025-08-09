# CS50 Final Project

This is my final project for CS50. I'm building a mobile-first web app for a local dog walking company to manage client bookings. The backend will use Python, Flask, and an SQL database, and the frontend will be HTML styled with Bootstrap CSS.

## App Users

The app will have different functionalities depending on the type of user. There are three user types: admin (the business owner), clients, and walkers (the staff). Accounts created via the 'register' UX default to 'client'. I'll create 'admin' and 'walker' accounts by editing the db manually to keep the scope managable. I'm also assuming (for simplicity) that client - dog is a one to one relationship. In reality, a client could have more than one dog and the business offers multi dog discounts.

### Admin
The admin can:
- Manage walkers; add or delete walkers from the roster
- Manage walker availability; if a walker is on the roster they are available by default, unless the admin marks them as unavailable on a particular day.
- Allocate bookings to walkers
- See monthly summaries, by walker, client or dog.

### Clients
CLients can:
- Register for an account
- Complete an onboarding flow to provide basic information including their address, pick up instructions and details about their dog.
- Manage bookings; clients can make new walk bookings, edit or cancel existing bookings.
- See a monthly walk summary and their invoice.

### Walkers




### Clients

Clients can:

- Register and manage their profile, including adding a profile picture, contact details, and specific instructions for pick-ups/drop-offs (e.g., codes to access their building, a Google Maps link to their address).
- Add and manage dogs associated with their account, including uploading a picture of the dog and providing basic info like breed, age, and allergies.
- Request to book services (currently either a 'walk', 'sitting', or 'drop-in'; additional services may be added in the future) for their dog. Walks can be booked as 'morning', 'afternoon', or 'both'.
- View, edit, and cancel their upcoming bookings.
- View their booking history and invoices for previous months.
- Opt in to notifications when their booking is confirmed or if the admin needs to override their booked preference (e.g., allocating their dog to a slot other than the one the client requested).

The app will enforce validation on client actions:

- Bookings will be limited to weekdays and up to 3 months in advance.
- Availability will be managed based on the number of slots available for each service on a given day.
- Cancellation policies will be enforced (e.g., walks canceled within 5 days will incur a 100% charge).
- Discounts will be applied automatically (e.g., a 10% discount if the client books both a 'morning' and 'afternoon' slot on a given day).

### Admin

The admin can:

- Manage availability for each service, such as setting the number of 'morning' and 'afternoon' walking slots available per day.
- Approve booking requests and override the client's preferred booking day or slot manually.
- Allocate bookings to team members (walkers) based on their availability. The allocation UX will feature a drag-and-drop interface, with each dog's booking represented as a draggable tile that can be dropped into a walker's morning or afternoon slot.
- Generate client invoices based on the services booked, including additional charges or discounts.
- Adjust pricing for different services.
- Generate payslips for each walker based on the number of dogs they have walked in a given month.
- View dashboards showing booking trends over time, bookings per day, revenue per month, etc.

### Walkers

Walkers can:

- View their schedule, including which dogs are allocated to them for the 'morning' and 'afternoon' of a given day.
- Access client-provided information about each dog, including a photo and basic details like allergies.
- View client-provided pick-up and drop-off instructions.

## App Optimization

The app will be optimized for different screen sizes:

- Clients and walkers will primarily use the app via a web browser on their mobile devices, so the app will be designed to work seamlessly on smaller screens.
- The admin will primarily use the app via a desktop web browser, so their views will target larger screen sizes.

## Tech Stack

The app will be built using the following technologies:

- **Backend**: Python and Flask for server-side logic and API development.
- **Database**: An SQL database for storing user, dog, booking, and service data.
- **Frontend**: React for building interactive user interfaces.
- **Styling**: Bootstrap for responsive and mobile-first design.

## Feature backlog

I've excluded some features to keep the project's scope managable. With more time and skill, I could add features to:

- Allow walkers to manage their own availability
- Generate pdf invoices
- Email invoices to clients
- Handle card payments in app
- Support multi-dog discounts, where a client has multiple dogs


## TODO
- Add a business name and logo using the favicon image
- Redo the login, registration and onboarding forms to use flask-wtf
- Get rid of the flash message between register and onboarding
- Change the onboarding h1 to something like 'welcome' w a hand wave emoji
- Add 'skip' functionality to the user onboarding form, so that users can register but defer completing their onboarding
- delete the 'other info' col from the dogs table, I'm not going to use that anymore.






