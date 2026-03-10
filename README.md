TODO:

Project Roadmap & Pending Tasks
Documentation & State Tracking: Create a "living" documentation file for Redis and SQL usage, detailing exactly what data is stored, known breaking points, and the logic for data eviction (TTL).

Architectural Layering: Refactor the API into distinct layers for each database; implement dedicated services/repositories for CRUD operations to ensure clean separation of concerns.

Proactive Caching: Implement a caching strategy where two weeks of calendar data is pre-loaded into Redis upon login. Ensure that when accessing the calendar page, data is fetched directly from the cache for instant loading, and cleared upon logout or app closure.

AI-Enhanced Treatment Sessions: * Integrate AI-driven acupuncture point recommendations based on the diagnosis.

Add a visual confidence bar for AI diagnostic certainty.

Redesign the Clinical Summary and ensure all AI insights are presented through a clean, modern, and polished visual interface on the treatment page.

Implement real-time diagnosis updates triggered by "Enter" in the Tongue & Pulse fields.

Persist treatment session notes in the database.

Bot Synchronization: Enable real-time updates between the Telegram bot and the calendar. Trigger a cache refresh and a notification/UI update immediately when a time slot is marked as "available."

Messaging & Notifications: Resolve "ghost notifications" in the messaging tab and integrate the UI with the bot to allow managing conversations directly from the app.

Bot Abstraction (Interface): Refactor the bot's architecture using an Interface pattern to allow for easy integration of additional platforms (e.g., WhatsApp) in the future.

Security Audit: Review sensitive Google account credentials within the .env file to ensure security best practices and clarify their necessity.