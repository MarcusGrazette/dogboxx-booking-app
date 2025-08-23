# This is...my CS50 Final Project

I'm building a mobile-first web app for a local dog walking company. I'm using Python, Flask, and an SQL database on the backend, and HTML styled with Bootstrap CSS for the front end. I expect that most clients will prefer an 'app like' experience, so will explore options for eventually deploying this as a progressive web app - to avoid app store fees. Some pages (eg admin.html) use AJAX to handle client - server interactions without needing to reload the page.

## Intended Users
There are three user types: admin (the business owner), clients, and walkers (the staff). Accounts created via the 'register' UX default to 'client'. I'll create 'admin' and 'walker' accounts by editing the db manually (I'm using phpMyAdmin for a more GUI-type experience than SQLite).

## Scope and assumptions
I've limited the scope and made some assuptions to simplify the problem to keep the project managable. I plan to submit a minimum viable poduct (MVP) version for CS50, then continue to build and test features until the product meets real-world business requirements.

The MVP features are:
- Clients can register and onboard (ie add their address and dog details)
- Clients can manage booking requestes (view, delete) - editing booking requests is not in scope.
- Admins can confirm or reject booking requests and allocate walkers to bookings
- Admins can see schedules for all walkers, and filter by walker by day and by week
- Walkers can view their daily and weekly walk schedule

I've made some assumptions to limit the problem space
- The client - dog relationship is one to one.
- There are three walkers, who always work full days (morning and afternoon)

## Feature Roadmap
I'm working with the company to build out a set of feature requirements. Sometimes, I'll need to do these by interacting directly with the db via sqlite or phpMyAdmin (eg adding walkers) to enable MVP functionality (eg walk booking flow won't work unless there are walkers in the db but there is no UX for adding walkers in the MVP). 

The feature roadmap could include:

### App level automation
- Allow the admin to generate invoices based on services, eg number of walks, taking additional charges or discounts (eg for clients with more than one dog or who book one dog into both the morning and afternoon slots)
- Enforce the cancellation policy, eg bookings with fewer than 5 days' notice will still be charged.

### Admin
The admin will be able to:
- Extend the service offering (adding different service types like pet sitting, drop ins, grooming)
- Manage walkers; add or delete walkers from the roster

### Clients
CLients can:
- Make recurring bookings, eg every Tuesday morning for the next 3 weeks.
- See a monthly walk summary 
- View their monthly invoice.
- Pay online (perhaps with Stripe integration?)

### Walkers
- Manage their availability, indicating which days and for which slots (morning and/or afternoon) they are availabe.
- See Google Maps directions to their next pickup (using the Google Maps API)

## Using AI


## User guide




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
- add a user_id col to the walkers table, by uncommenting the lines in models.
- date manip, how to get the week based on the date selected.
- Move flash messages into toasts? Avoids layout shift..
- check my redirects use url_for not hardcoded.
- add a limit to the number of bookings per day
- build out the 'walkers' card on admin.html
- add an 'admin' user who isn't also a client

- Get rid of the flash message between register and onboarding
- Change the onboarding h1 to something like 'welcome' w a hand wave emoji

- Add 'skip' functionality to the user onboarding form, so that users can register but defer completing their onboarding
 
- check why I get a 403 error persistantly when the server stops and restarts


- add options for the user to see all upcoming walks? or add pagination?


ERRORS
- booking form validation - stop duplicate bookings where dog + day + slot are the same.
- the 'remember me' feature on the login page doesn't work as expected







