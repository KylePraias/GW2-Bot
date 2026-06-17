import React, { useEffect } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import Navbar from "./components/Navbar";
import Footer from "./components/Footer";
import Home from "./pages/Home";
import HowToAdd from "./pages/HowToAdd";
import Features from "./pages/Features";
import Commands from "./pages/Commands";

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

export default function App() {
  return (
    <div className="min-h-screen bg-stone-950 text-stone-100 flex flex-col">
      <ScrollToTop />
      <Navbar />
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/how-to-add" element={<HowToAdd />} />
          <Route path="/features" element={<Features />} />
          <Route path="/commands" element={<Commands />} />
        </Routes>
      </main>
      <Footer />
      <Toaster
        position="bottom-right"
        theme="dark"
        toastOptions={{
          style: {
            background: "#1c1917",
            border: "1px solid rgba(245,158,11,0.3)",
            color: "#f5f5f4",
            fontFamily: "Outfit, sans-serif",
          },
        }}
      />
    </div>
  );
}
