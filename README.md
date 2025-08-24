# Bermondsey Street Dog Walkers
#### Video Demo:  https://youtu.be/0S2YE_XecjI
#### Description: A booking site for a dog walking company.


## This is...my CS50 Final Project

I'm building a mobile-first web app for a local dog walking company. I'm using Python, Flask, and an SQL database on the backend, and HTML styled with Bootstrap CSS for the front end. I expect that most clients will prefer an 'app like' experience, so will explore options for eventually deploying this as a progressive web app - to avoid app store fees. Some pages (eg admin.html) use AJAX to handle client - server interactions without needing to reload the page.

### Intended Users
There are three user types: admin (the business owner), clients, and walkers (the staff). Accounts created via the 'register' UX default to 'client'. I'll create 'admin' and 'walker' accounts by editing the db manually (I'm using phpMyAdmin for a more GUI-type experience than SQLite).

### Scope and assumptions
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

### Feature Roadmap
I'm working with the company to build out a set of feature requirements. Sometimes, I'll need to do these by interacting directly with the db via sqlite or phpMyAdmin (eg adding walkers) to enable MVP functionality (eg walk booking flow won't work unless there are walkers in the db but there is no UX for adding walkers in the MVP). 

The feature roadmap could include:

#### App level automation
- Allow the admin to generate invoices based on services, eg number of walks, taking additional charges or discounts (eg for clients with more than one dog or who book one dog into both the morning and afternoon slots)
- Enforce the cancellation policy, eg bookings with fewer than 5 days' notice will still be charged.

#### Admin
The admin will be able to:
- Extend the service offering (adding different service types like pet sitting, drop ins, grooming)
- Manage walkers; add or delete walkers from the roster

#### Clients
CLients can:
- Make recurring bookings, eg every Tuesday morning for the next 3 weeks.
- See a monthly walk summary 
- View their monthly invoice.
- Pay online (perhaps with Stripe integration?)

#### Walkers
- Manage their availability, indicating which days and for which slots (morning and/or afternoon) they are availabe.
- See Google Maps directions to their next pickup (using the Google Maps API)

### Using AI






