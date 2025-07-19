# CS50 Final Project
This is my final project for CS50. I'm going to build a mobile-first web app for a (real) local dog walking company to manage client bookings. I'll use Python, Flask and an SQL databased on the backend, and an interactive frontend with React and Boostrap for styling.

## Features and users
The app should have different functions depending on the type of user. There are three user types: admin (the business owner), clients and walkers (the staff)

Clients can:

Register and manage their profile, including adding a profile picture, adding contact details, specific instructions for pick ups / drop offs (eg codes to access their building, a google maps link to their address).

Add and manage dogs associated with their account, including adding a picture of the dog, basic info like breed, age and allergies.

Request to book services (currently either a 'walk', 'sitting' or 'drop in', there may be more services in future) for their dog. Walks can be booked as 'morning', 'afternoon' or 'both'.

View, edit and cancel their bookings.

The app should layer some validation onto user actions:

Bookings should be limited to weekdays, up to 3 months in advance. The app should manage availablity, based on the number of slots available for each service on the give day. It should also enforce the cancellation policy (eg walks cancelled within 5 days will still incur a charge).

The admin can:

Approve requests to book.

Allocate bookings to team members (walkers), based on which walkers are available on a given day.

Generate client invoices, based on the services that the client has booked, with any additional charges or discounts.

Adjust the pricing for different services.

Generate payslips for each walker, based on the number of dogs they have walked in a given month. 

See some nice dashbords showing booking trends over time, bookings per day, revenue per month etc. 

Walkers can:

See their schedule (ie which dogs are allocated to them for the 'morning' and 'afternoon' of a given day).

See client-provided information about each dog, including a photo, basic information like allergies. 

See client-provided pick up and drop off instructions.

## App optimisation

Clients and walkers will mostly use the app via a web browser on thier mobile, so the app should be optmised to work on smaller screens. The admin will mostly use the app via a desktop web browser, so their views can target larger screen sizes.





