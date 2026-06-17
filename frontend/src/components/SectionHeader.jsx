import React from "react";
import { motion } from "framer-motion";

const fade = {
  hidden: { opacity: 0, y: 24 },
  show: (i = 0) => ({
    opacity: 1,
    y: 0,
    transition: { duration: 0.6, delay: i * 0.08, ease: "easeOut" },
  }),
};

export function SectionHeader({ overline, title, sub, center = false }) {
  return (
    <div className={`max-w-3xl ${center ? "mx-auto text-center" : ""}`}>
      {overline && (
        <motion.p
          variants={fade}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true }}
          className="font-cinzel text-xs tracking-[0.3em] uppercase text-amber-500 mb-4"
        >
          {overline}
        </motion.p>
      )}
      <motion.h2
        variants={fade}
        custom={1}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true }}
        className="font-display font-bold text-4xl sm:text-5xl lg:text-6xl text-stone-100 leading-[1.05]"
      >
        {title}
      </motion.h2>
      {sub && (
        <motion.p
          variants={fade}
          custom={2}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true }}
          className="mt-6 text-lg text-stone-400 font-light leading-relaxed"
        >
          {sub}
        </motion.p>
      )}
      <motion.div
        variants={fade}
        custom={3}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true }}
        className={`mt-8 h-px w-40 gold-rule ${center ? "mx-auto" : ""}`}
      />
    </div>
  );
}

export { fade };
